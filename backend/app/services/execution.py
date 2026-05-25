from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import delete, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db.session import SessionLocal
from app.models.approval import ChangeTicket, ChangeTicketStatement, ExecutionJob, ExecutionLock, ExecutionStatementResult
from app.models.auth import UserIdentity
from app.services.audit import AuditActor, AuditService

FAILURE_INDEX_RE = re.compile(r"Statement\s+(?P<index>\d+)\s+failed:", re.IGNORECASE)


class DbxExecutionClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def execute_in_transaction(
        self,
        *,
        datasource_id: str,
        database: str,
        schema: str | None,
        statements: list[str],
    ) -> dict:
        url = f"{self.settings.dbx_web_base_url.rstrip('/')}/api/internal/query/execute-in-transaction"
        headers = {"X-DBX-Internal-Token": self.settings.dbx_web_internal_token}
        payload = {
            "connectionId": datasource_id,
            "database": database,
            "schema": schema,
            "statements": statements,
        }
        async with httpx.AsyncClient(timeout=300.0) as client:
            response = await client.post(url, json=payload, headers=headers)
        if not response.is_success:
            detail = response.text.strip() or f"dbx-web returned {response.status_code}"
            raise RuntimeError(detail)
        data = response.json()
        if not isinstance(data, dict):
            raise RuntimeError("dbx-web returned an invalid execution payload")
        return data


class ExecutionService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.client = DbxExecutionClient(self.settings)
        self.audit_service = AuditService()

    def ensure_job_for_ticket(
        self,
        db: Session,
        ticket: ChangeTicket,
        *,
        run_key: str,
        executor_user_id: str | None = None,
        execution_mode: str = "immediate",
    ) -> ExecutionJob:
        existing = db.execute(
            select(ExecutionJob).where(
                ExecutionJob.ticket_id == ticket.id,
                ExecutionJob.run_key == run_key,
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing

        job = ExecutionJob(
            ticket_id=ticket.id,
            run_key=run_key,
            status="queued",
            executor_type="user" if executor_user_id else "system",
            executor_user_id=executor_user_id,
            execution_mode=execution_mode,
        )
        db.add(job)
        db.flush()

        ticket.last_execution_job_id = job.id
        ticket.current_status = "queued"
        actor = self._build_actor_for_job(db, job, ticket)
        self.audit_service.record_event(
            db,
            event_type="execution.job.queued",
            category="execution",
            action="queue",
            actor=actor,
            resource_type="execution_job",
            resource_id=job.id,
            resource_name=ticket.ticket_no,
            payload={
                "ticket_id": ticket.id,
                "ticket_no": ticket.ticket_no,
                "run_key": run_key,
                "execution_mode": execution_mode,
                "executor_type": job.executor_type,
            },
        )
        return job

    def enqueue_due_tickets(self, db: Session) -> list[str]:
        now = datetime.now(UTC)
        due_tickets = db.execute(
            select(ChangeTicket).where(
                ChangeTicket.current_status == "approved",
                or_(ChangeTicket.scheduled_at.is_(None), ChangeTicket.scheduled_at <= now),
            )
        ).scalars().all()
        job_ids: list[str] = []
        for ticket in due_tickets:
            job = self.ensure_job_for_ticket(
                db,
                ticket,
                run_key=f"scheduled:{ticket.id}",
                execution_mode="scheduled" if ticket.scheduled_at else "immediate",
            )
            job_ids.append(job.id)
        if job_ids:
            db.commit()
        return job_ids

    async def run_job(self, job_id: str) -> None:
        db = SessionLocal()
        try:
            job = db.get(ExecutionJob, job_id)
            if job is None or job.status != "queued":
                return

            ticket = db.get(ChangeTicket, job.ticket_id)
            if ticket is None:
                return

            statements = db.execute(
                select(ChangeTicketStatement)
                .where(ChangeTicketStatement.ticket_id == ticket.id)
                .order_by(ChangeTicketStatement.statement_order.asc())
            ).scalars().all()
            if not statements:
                job.status = "failed"
                job.error_message = "Ticket contains no executable statements"
                job.finished_at = datetime.now(UTC)
                ticket.current_status = "failed"
                self.audit_service.record_event(
                    db,
                    event_type="execution.job.failed",
                    category="execution",
                    action="execute",
                    outcome="failure",
                    actor=self._build_actor_for_job(db, job, ticket),
                    resource_type="execution_job",
                    resource_id=job.id,
                    resource_name=ticket.ticket_no,
                    payload={"ticket_id": ticket.id, "reason": "Ticket contains no executable statements"},
                )
                db.commit()
                return

            if not self._acquire_locks(db, job, ticket):
                return

            job.status = "running"
            job.started_at = datetime.now(UTC)
            ticket.current_status = "executing"
            self.audit_service.record_event(
                db,
                event_type="execution.job.started",
                category="execution",
                action="execute",
                actor=self._build_actor_for_job(db, job, ticket),
                resource_type="execution_job",
                resource_id=job.id,
                resource_name=ticket.ticket_no,
                payload={"ticket_id": ticket.id, "run_key": job.run_key},
            )
            db.commit()

            statement_texts = [statement.statement_text for statement in statements]
            try:
                result = await self.client.execute_in_transaction(
                    datasource_id=ticket.datasource_id,
                    database=ticket.target_database,
                    schema=ticket.target_schema,
                    statements=statement_texts,
                )
            except Exception as error:
                self._record_failure(db, job_id, str(error), statements)
                return

            self._record_success(db, job_id, result, statements)
        finally:
            self._release_locks(job_id)
            db.close()

    def _acquire_locks(self, db: Session, job: ExecutionJob, ticket: ChangeTicket) -> bool:
        now = datetime.now(UTC)
        db.execute(delete(ExecutionLock).where(ExecutionLock.expires_at < now))
        expires_at = now + timedelta(seconds=self.settings.approval_execution_lock_seconds)
        try:
            db.add(
                ExecutionLock(
                    resource_type="ticket",
                    resource_key=ticket.id,
                    owner_job_id=job.id,
                    expires_at=expires_at,
                )
            )
            db.add(
                ExecutionLock(
                    resource_type="datasource",
                    resource_key=ticket.datasource_id,
                    owner_job_id=job.id,
                    expires_at=expires_at,
                )
            )
            db.commit()
            return True
        except IntegrityError:
            db.rollback()
            fresh_job = db.get(ExecutionJob, job.id)
            fresh_ticket = db.get(ChangeTicket, ticket.id)
            if fresh_job is not None:
                fresh_job.status = "cancelled"
                fresh_job.error_message = "Duplicate execution prevented by execution lock"
                fresh_job.finished_at = now
            if fresh_ticket is not None:
                fresh_ticket.current_status = "approved"
            if fresh_job is not None:
                self.audit_service.record_event(
                    db,
                    event_type="execution.job.lock_conflict",
                    category="execution",
                    action="execute",
                    outcome="failure",
                    actor=self._build_actor_for_job(db, fresh_job, fresh_ticket or ticket),
                    resource_type="execution_job",
                    resource_id=fresh_job.id,
                    resource_name=(fresh_ticket or ticket).ticket_no,
                    payload={
                        "ticket_id": (fresh_ticket or ticket).id,
                        "datasource_id": (fresh_ticket or ticket).datasource_id,
                        "reason": "Duplicate execution prevented by execution lock",
                    },
                )
            db.commit()
            return False

    def _record_success(
        self,
        db: Session,
        job_id: str,
        result: dict,
        statements: list[ChangeTicketStatement],
    ) -> None:
        job = db.get(ExecutionJob, job_id)
        if job is None:
            return
        ticket = db.get(ChangeTicket, job.ticket_id)
        now = datetime.now(UTC)
        total_affected = result.get("affected_rows")
        execution_time_ms = result.get("execution_time_ms")
        for statement in statements:
            db.add(
                ExecutionStatementResult(
                    job_id=job.id,
                    statement_order=statement.statement_order,
                    statement_text=statement.statement_text,
                    success=True,
                    affected_rows=total_affected if statement.statement_order == len(statements) else None,
                    duration_ms=execution_time_ms if statement.statement_order == len(statements) else None,
                    result=result if statement.statement_order == len(statements) else {"rolled_into_transaction": True},
                )
            )

        job.status = "succeeded"
        job.finished_at = now
        job.result_summary = result
        if ticket is not None:
            ticket.current_status = "succeeded"
            ticket.executed_at = now
        actor = self._build_actor_for_job(db, job, ticket)
        self.audit_service.record_event(
            db,
            event_type="execution.job.succeeded",
            category="execution",
            action="execute",
            actor=actor,
            resource_type="execution_job",
            resource_id=job.id,
            resource_name=ticket.ticket_no if ticket is not None else job.id,
            payload={
                "ticket_id": ticket.id if ticket is not None else job.ticket_id,
                "run_key": job.run_key,
                "execution_mode": job.execution_mode,
                "affected_rows": self._to_int(total_affected),
            },
        )
        if ticket is not None:
            self.audit_service.record_query(
                db,
                actor=actor,
                execution_id=job.id,
                datasource_id=ticket.datasource_id,
                database_name=ticket.target_database,
                schema_name=ticket.target_schema,
                table_name=ticket.target_table,
                operation_type="approval_execution",
                execution_mode=job.execution_mode,
                statement_count=len(statements),
                sql_text=";\n".join(statement.statement_text for statement in statements),
                status="succeeded",
                duration_ms=self._to_int(execution_time_ms),
                affected_rows=self._to_int(total_affected),
                metadata={
                    "ticket_id": ticket.id,
                    "ticket_no": ticket.ticket_no,
                    "job_id": job.id,
                    "run_key": job.run_key,
                },
                completed_at=now,
            )
        db.commit()

    def _record_failure(
        self,
        db: Session,
        job_id: str,
        error_message: str,
        statements: list[ChangeTicketStatement],
    ) -> None:
        job = db.get(ExecutionJob, job_id)
        if job is None:
            return
        ticket = db.get(ChangeTicket, job.ticket_id)
        now = datetime.now(UTC)
        failure_index = self._extract_failure_index(error_message)
        for statement in statements:
            if failure_index is None:
                success = False
                result = {"not_executed": True}
                statement_error = error_message if statement.statement_order == 1 else None
            elif statement.statement_order < failure_index:
                success = True
                result = {"rolled_back": True}
                statement_error = None
            elif statement.statement_order == failure_index:
                success = False
                result = {"failed": True}
                statement_error = error_message
            else:
                success = False
                result = {"not_executed": True}
                statement_error = None

            db.add(
                ExecutionStatementResult(
                    job_id=job.id,
                    statement_order=statement.statement_order,
                    statement_text=statement.statement_text,
                    success=success,
                    db_error_message=statement_error,
                    result=result,
                )
            )

        job.status = "failed"
        job.finished_at = now
        job.error_message = error_message
        job.result_summary = {"error": error_message}
        if ticket is not None:
            ticket.current_status = "failed"
        actor = self._build_actor_for_job(db, job, ticket)
        self.audit_service.record_event(
            db,
            event_type="execution.job.failed",
            category="execution",
            action="execute",
            outcome="failure",
            actor=actor,
            resource_type="execution_job",
            resource_id=job.id,
            resource_name=ticket.ticket_no if ticket is not None else job.id,
            payload={
                "ticket_id": ticket.id if ticket is not None else job.ticket_id,
                "run_key": job.run_key,
                "execution_mode": job.execution_mode,
                "error_message": error_message,
            },
        )
        if ticket is not None:
            self.audit_service.record_query(
                db,
                actor=actor,
                execution_id=job.id,
                datasource_id=ticket.datasource_id,
                database_name=ticket.target_database,
                schema_name=ticket.target_schema,
                table_name=ticket.target_table,
                operation_type="approval_execution",
                execution_mode=job.execution_mode,
                statement_count=len(statements),
                sql_text=";\n".join(statement.statement_text for statement in statements),
                status="failed",
                error_message=error_message,
                metadata={
                    "ticket_id": ticket.id,
                    "ticket_no": ticket.ticket_no,
                    "job_id": job.id,
                    "run_key": job.run_key,
                },
                completed_at=now,
            )
        db.commit()

    def _release_locks(self, job_id: str) -> None:
        db = SessionLocal()
        try:
            db.execute(delete(ExecutionLock).where(ExecutionLock.owner_job_id == job_id))
            db.commit()
        finally:
            db.close()

    def _extract_failure_index(self, error_message: str) -> int | None:
        match = FAILURE_INDEX_RE.search(error_message)
        if not match:
            return None
        return int(match.group("index"))

    def _build_actor_for_job(
        self,
        db: Session,
        job: ExecutionJob,
        ticket: ChangeTicket | None,
    ) -> AuditActor:
        actor_user_id = job.executor_user_id or (ticket.submitter_id if ticket is not None else None)
        user = db.get(UserIdentity, actor_user_id) if actor_user_id else None
        return self.audit_service.build_actor(user=user)

    def _to_int(self, value: object) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
