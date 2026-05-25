from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.models.approval import (
    ApprovalAction,
    ApprovalFlow,
    ApprovalFlowStep,
    ApprovalInstance,
    ApprovalInstanceStep,
    ChangeTicket,
    ChangeTicketStatement,
    ExecutionJob,
    ExecutionStatementResult,
)
from app.models.auth import UserIdentity
from app.services.authorization import AccessContext, AuthorizationService

DDL_KEYWORDS = {
    "alter",
    "comment",
    "create",
    "drop",
    "grant",
    "rename",
    "revoke",
    "truncate",
}
DML_KEYWORDS = {
    "delete",
    "insert",
    "merge",
    "replace",
    "update",
}
HIGH_RISK_KEYWORDS = {
    "delete",
    "drop",
    "replace",
    "revoke",
    "truncate",
}


@dataclass(frozen=True)
class ClassifiedStatement:
    order: int
    text: str
    statement_type: str
    risk_level: str
    risk_tags: list[str]


@dataclass(frozen=True)
class TicketBundle:
    ticket: ChangeTicket
    statements: list[ChangeTicketStatement]
    approval_instance: ApprovalInstance | None
    approval_steps: list[ApprovalInstanceStep]
    approval_actions: dict[str, list[ApprovalAction]]
    execution_jobs: list[ExecutionJob]
    execution_results: dict[str, list[ExecutionStatementResult]]


@dataclass(frozen=True)
class TicketTransitionResult:
    ticket: ChangeTicket
    should_queue_execution: bool = False


class SqlStatementSplitter:
    def __init__(self) -> None:
        self.buffer = ""
        self.in_single_quote = False
        self.in_double_quote = False
        self.in_backtick = False
        self.in_line_comment = False
        self.in_block_comment = False
        self.previous: str | None = None

    def push_chunk(self, chunk: str) -> list[str]:
        statements: list[str] = []
        chars = list(chunk)
        index = 0
        while index < len(chars):
            char = chars[index]
            nxt = chars[index + 1] if index + 1 < len(chars) else None

            if self.in_line_comment:
                self.buffer += char
                if char == "\n":
                    self.in_line_comment = False
                self.previous = char
                index += 1
                continue

            if self.in_block_comment:
                self.buffer += char
                if self.previous == "*" and char == "/":
                    self.in_block_comment = False
                self.previous = char
                index += 1
                continue

            if not self.in_single_quote and not self.in_double_quote and not self.in_backtick:
                if char == "-" and nxt == "-":
                    self.buffer += char
                    self.previous = char
                    self.in_line_comment = True
                    index += 1
                    continue
                if char == "/" and nxt == "*":
                    self.buffer += char
                    self.previous = char
                    self.in_block_comment = True
                    index += 1
                    continue

            self.buffer += char
            if char == "'" and not self.in_double_quote and not self.in_backtick and self.previous != "\\":
                self.in_single_quote = not self.in_single_quote
            elif char == '"' and not self.in_single_quote and not self.in_backtick and self.previous != "\\":
                self.in_double_quote = not self.in_double_quote
            elif char == "`" and not self.in_single_quote and not self.in_double_quote and self.previous != "\\":
                self.in_backtick = not self.in_backtick
            elif (
                char == ";"
                and not self.in_single_quote
                and not self.in_double_quote
                and not self.in_backtick
                and not self.in_line_comment
                and not self.in_block_comment
            ):
                statement = self.buffer[:-1].strip()
                self.buffer = ""
                if statement:
                    statements.append(statement)
                self.previous = None
                index += 1
                continue

            self.previous = char
            index += 1
        return statements

    def finish(self) -> list[str]:
        remaining = self.buffer.strip()
        self.buffer = ""
        self.previous = None
        self.in_single_quote = False
        self.in_double_quote = False
        self.in_backtick = False
        self.in_line_comment = False
        self.in_block_comment = False
        return [remaining] if remaining else []


class ApprovalService:
    def sync_builtin_flows(self, db: Session) -> None:
        flow = db.execute(
            select(ApprovalFlow).where(ApprovalFlow.code == "builtin-sql-change-admin")
        ).scalar_one_or_none()
        if flow is None:
            flow = ApprovalFlow(code="builtin-sql-change-admin")
            db.add(flow)
            db.flush()

        flow.name = "Built-in SQL Change Approval"
        flow.description = "Default approval template for DDL/DML tickets routed to admin reviewers."
        flow.ticket_type = "sql_change"
        flow.match_rule = {"ticket_types": ["ddl", "dml", "mixed"]}
        flow.enabled = True
        flow.built_in = True
        flow.version = 1

        step = db.execute(
            select(ApprovalFlowStep).where(
                ApprovalFlowStep.flow_id == flow.id,
                ApprovalFlowStep.step_no == 1,
            )
        ).scalar_one_or_none()
        if step is None:
            step = ApprovalFlowStep(flow_id=flow.id, step_no=1)
            db.add(step)

        step.step_name = "Admin Approval"
        step.approval_mode = "any_one"
        step.approver_type = "role"
        step.approver_ref = "admin"
        step.rule = {"allow_submitter_self_approval": True}

        db.commit()

    def list_flows(self, db: Session) -> list[tuple[ApprovalFlow, list[ApprovalFlowStep]]]:
        flows = db.execute(select(ApprovalFlow).order_by(ApprovalFlow.code.asc())).scalars().all()
        result: list[tuple[ApprovalFlow, list[ApprovalFlowStep]]] = []
        for flow in flows:
            steps = db.execute(
                select(ApprovalFlowStep)
                .where(ApprovalFlowStep.flow_id == flow.id)
                .order_by(ApprovalFlowStep.step_no.asc())
            ).scalars().all()
            result.append((flow, steps))
        return result

    def list_visible_tickets(
        self,
        db: Session,
        current_user: UserIdentity,
        access_context: AccessContext,
        *,
        scope: str,
    ) -> list[ChangeTicket]:
        tickets = db.execute(select(ChangeTicket).order_by(desc(ChangeTicket.created_at))).scalars().all()
        visible: list[ChangeTicket] = []
        for ticket in tickets:
            bundle = self.load_ticket_bundle(db, ticket.id)
            if scope == "my" and ticket.submitter_id != current_user.id and "admin" not in access_context.roles:
                continue
            if scope == "pending":
                pending_step = self.current_pending_step(bundle)
                if pending_step is None or not self.is_step_actionable(pending_step, access_context):
                    continue
            if self.can_view_ticket(bundle, current_user, access_context):
                visible.append(ticket)
        return visible

    def create_ticket(
        self,
        db: Session,
        current_user: UserIdentity,
        access_context: AccessContext,
        authorization_service: AuthorizationService,
        *,
        title: str,
        datasource_id: str,
        target_database: str,
        target_schema: str | None,
        target_table: str | None,
        sql_text: str,
        scheduled_at: datetime | None,
    ) -> ChangeTicket:
        classified_statements = self.classify_sql(sql_text)
        self._ensure_scope_access(
            authorization_service,
            access_context,
            datasource_id=datasource_id,
            database=target_database,
            schema=target_schema,
            table=target_table,
        )

        ticket = ChangeTicket(
            ticket_no=self._build_ticket_number(),
            ticket_type=self._resolve_ticket_type(classified_statements),
            title=title.strip(),
            datasource_id=datasource_id.strip(),
            target_database=target_database.strip(),
            target_schema=target_schema.strip() if target_schema else None,
            target_table=target_table.strip() if target_table else None,
            risk_level=self._resolve_ticket_risk(classified_statements),
            sql_text=sql_text.strip(),
            sql_summary=self._summarize_sql(classified_statements),
            submitter_id=current_user.id,
            scheduled_at=scheduled_at,
        )
        db.add(ticket)
        db.flush()

        for statement in classified_statements:
            db.add(
                ChangeTicketStatement(
                    ticket_id=ticket.id,
                    statement_order=statement.order,
                    statement_text=statement.text,
                    statement_type=statement.statement_type,
                    risk_tags=statement.risk_tags,
                    risk_level=statement.risk_level,
                )
            )

        db.commit()
        db.refresh(ticket)
        return ticket

    def submit_ticket(
        self,
        db: Session,
        ticket_id: str,
        current_user: UserIdentity,
        access_context: AccessContext,
        authorization_service: AuthorizationService,
    ) -> TicketTransitionResult:
        bundle = self.load_ticket_bundle(db, ticket_id)
        ticket = bundle.ticket
        self._ensure_draft_owner(ticket, current_user, access_context)
        if ticket.current_status != "draft":
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Only draft tickets can be submitted")

        self._ensure_scope_access(
            authorization_service,
            access_context,
            datasource_id=ticket.datasource_id,
            database=ticket.target_database,
            schema=ticket.target_schema,
            table=ticket.target_table,
        )

        flow = self._match_flow(db, ticket.ticket_type)
        if flow is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No approval flow matches this ticket")

        flow_steps = db.execute(
            select(ApprovalFlowStep)
            .where(ApprovalFlowStep.flow_id == flow.id)
            .order_by(ApprovalFlowStep.step_no.asc())
        ).scalars().all()
        if not flow_steps:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Approval flow has no steps")

        instance = ApprovalInstance(ticket_id=ticket.id, flow_id=flow.id, status="pending", current_step_no=1)
        db.add(instance)
        db.flush()

        now = datetime.now(UTC)
        for flow_step in flow_steps:
            db.add(
                ApprovalInstanceStep(
                    instance_id=instance.id,
                    flow_step_id=flow_step.id,
                    step_no=flow_step.step_no,
                    step_name=flow_step.step_name,
                    approval_mode=flow_step.approval_mode,
                    approver_type=flow_step.approver_type,
                    approver_ref=flow_step.approver_ref,
                    rule=flow_step.rule,
                    status="pending" if flow_step.step_no == 1 else "waiting",
                    started_at=now if flow_step.step_no == 1 else None,
                )
            )

        ticket.current_status = "pending_approval"
        ticket.submitted_at = now
        ticket.latest_flow_instance_id = instance.id

        db.commit()
        db.refresh(ticket)
        return TicketTransitionResult(ticket=ticket)

    def approve_ticket(
        self,
        db: Session,
        ticket_id: str,
        current_user: UserIdentity,
        access_context: AccessContext,
        *,
        comment: str | None,
    ) -> TicketTransitionResult:
        bundle = self.load_ticket_bundle(db, ticket_id)
        ticket = bundle.ticket
        pending_step = self.current_pending_step(bundle)
        if pending_step is None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Ticket has no pending approval step")
        if not self.is_step_actionable(pending_step, access_context):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Current user cannot approve this step")

        now = datetime.now(UTC)
        db.add(
            ApprovalAction(
                instance_step_id=pending_step.id,
                actor_user_id=current_user.id,
                action="approve",
                comment=comment,
                payload={},
            )
        )
        pending_step.status = "approved"
        pending_step.finished_at = now

        instance = bundle.approval_instance
        if instance is None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Approval instance missing")

        next_step = self._next_waiting_step(bundle, pending_step.step_no)
        should_queue_execution = False
        if next_step is None:
            instance.status = "approved"
            instance.current_step_no = None
            instance.finished_at = now
            ticket.current_status = "approved"
            ticket.approved_at = now
            should_queue_execution = ticket.scheduled_at is None or ticket.scheduled_at <= now
        else:
            next_step.status = "pending"
            next_step.started_at = now
            instance.current_step_no = next_step.step_no
            ticket.current_status = "pending_approval"

        db.commit()
        db.refresh(ticket)
        return TicketTransitionResult(ticket=ticket, should_queue_execution=should_queue_execution)

    def reject_ticket(
        self,
        db: Session,
        ticket_id: str,
        current_user: UserIdentity,
        access_context: AccessContext,
        *,
        comment: str | None,
    ) -> TicketTransitionResult:
        bundle = self.load_ticket_bundle(db, ticket_id)
        ticket = bundle.ticket
        pending_step = self.current_pending_step(bundle)
        if pending_step is None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Ticket has no pending approval step")
        if not self.is_step_actionable(pending_step, access_context):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Current user cannot reject this step")

        now = datetime.now(UTC)
        db.add(
            ApprovalAction(
                instance_step_id=pending_step.id,
                actor_user_id=current_user.id,
                action="reject",
                comment=comment,
                payload={},
            )
        )
        pending_step.status = "rejected"
        pending_step.finished_at = now

        instance = bundle.approval_instance
        if instance is None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Approval instance missing")
        instance.status = "rejected"
        instance.current_step_no = None
        instance.finished_at = now
        ticket.current_status = "rejected"

        db.commit()
        db.refresh(ticket)
        return TicketTransitionResult(ticket=ticket)

    def load_ticket_bundle(self, db: Session, ticket_id: str) -> TicketBundle:
        ticket = db.get(ChangeTicket, ticket_id)
        if ticket is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")

        statements = db.execute(
            select(ChangeTicketStatement)
            .where(ChangeTicketStatement.ticket_id == ticket.id)
            .order_by(ChangeTicketStatement.statement_order.asc())
        ).scalars().all()

        approval_instance = None
        approval_steps: list[ApprovalInstanceStep] = []
        approval_actions: dict[str, list[ApprovalAction]] = {}
        if ticket.latest_flow_instance_id:
            approval_instance = db.get(ApprovalInstance, ticket.latest_flow_instance_id)
            if approval_instance is not None:
                approval_steps = db.execute(
                    select(ApprovalInstanceStep)
                    .where(ApprovalInstanceStep.instance_id == approval_instance.id)
                    .order_by(ApprovalInstanceStep.step_no.asc())
                ).scalars().all()
                step_ids = [step.id for step in approval_steps]
                if step_ids:
                    actions = db.execute(
                        select(ApprovalAction)
                        .where(ApprovalAction.instance_step_id.in_(step_ids))
                        .order_by(ApprovalAction.created_at.asc())
                    ).scalars().all()
                    for action in actions:
                        approval_actions.setdefault(action.instance_step_id, []).append(action)

        execution_jobs = db.execute(
            select(ExecutionJob)
            .where(ExecutionJob.ticket_id == ticket.id)
            .order_by(ExecutionJob.created_at.desc())
        ).scalars().all()
        execution_results: dict[str, list[ExecutionStatementResult]] = {}
        job_ids = [job.id for job in execution_jobs]
        if job_ids:
            results = db.execute(
                select(ExecutionStatementResult)
                .where(ExecutionStatementResult.job_id.in_(job_ids))
                .order_by(
                    ExecutionStatementResult.job_id.asc(),
                    ExecutionStatementResult.statement_order.asc(),
                )
            ).scalars().all()
            for result in results:
                execution_results.setdefault(result.job_id, []).append(result)

        return TicketBundle(
            ticket=ticket,
            statements=statements,
            approval_instance=approval_instance,
            approval_steps=approval_steps,
            approval_actions=approval_actions,
            execution_jobs=execution_jobs,
            execution_results=execution_results,
        )

    def get_visible_bundle(
        self,
        db: Session,
        ticket_id: str,
        current_user: UserIdentity,
        access_context: AccessContext,
    ) -> TicketBundle:
        bundle = self.load_ticket_bundle(db, ticket_id)
        if not self.can_view_ticket(bundle, current_user, access_context):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")
        return bundle

    def can_view_ticket(
        self,
        bundle: TicketBundle,
        current_user: UserIdentity,
        access_context: AccessContext,
    ) -> bool:
        if "admin" in access_context.roles:
            return True
        if bundle.ticket.submitter_id == current_user.id:
            return True
        pending_step = self.current_pending_step(bundle)
        return pending_step is not None and self.is_step_actionable(pending_step, access_context)

    def current_pending_step(self, bundle: TicketBundle) -> ApprovalInstanceStep | None:
        for step in bundle.approval_steps:
            if step.status == "pending":
                return step
        return None

    def is_step_actionable(self, step: ApprovalInstanceStep, access_context: AccessContext) -> bool:
        if step.status != "pending":
            return False
        if "admin" in access_context.roles:
            return True
        if step.approver_type == "role":
            return step.approver_ref in access_context.roles
        if step.approver_type == "user":
            return step.approver_ref == access_context.user.id
        return False

    def available_actions(
        self,
        bundle: TicketBundle,
        current_user: UserIdentity,
        access_context: AccessContext,
    ) -> list[str]:
        actions: list[str] = []
        ticket = bundle.ticket
        if ticket.current_status == "draft" and (ticket.submitter_id == current_user.id or "admin" in access_context.roles):
            actions.append("submit")

        pending_step = self.current_pending_step(bundle)
        if pending_step is not None and self.is_step_actionable(pending_step, access_context):
            actions.extend(["approve", "reject"])

        if ticket.current_status in {"approved", "failed"} and (
            ticket.submitter_id == current_user.id or "admin" in access_context.roles
        ):
            actions.append("retry")

        return actions

    def classify_sql(self, sql_text: str) -> list[ClassifiedStatement]:
        splitter = SqlStatementSplitter()
        statements = splitter.push_chunk(sql_text)
        statements.extend(splitter.finish())
        if not statements:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="SQL payload is empty")

        classified: list[ClassifiedStatement] = []
        for index, statement in enumerate(statements, start=1):
            keyword = self._leading_keyword(statement)
            if keyword in DDL_KEYWORDS:
                statement_type = "ddl"
            elif keyword in DML_KEYWORDS:
                statement_type = "dml"
            else:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Unsupported statement type in ticket: {keyword or 'unknown'}",
                )

            risk_tags: list[str] = []
            risk_level = "medium"
            if keyword in HIGH_RISK_KEYWORDS:
                risk_tags.append("destructive")
                risk_level = "high"
            if "where" not in statement.lower() and keyword in {"delete", "update"}:
                risk_tags.append("no_where_clause")
                risk_level = "high"
            if statement_type == "ddl":
                risk_tags.append("structure_change")

            classified.append(
                ClassifiedStatement(
                    order=index,
                    text=statement.strip(),
                    statement_type=statement_type,
                    risk_level=risk_level,
                    risk_tags=risk_tags,
                )
            )
        return classified

    def _ensure_scope_access(
        self,
        authorization_service: AuthorizationService,
        access_context: AccessContext,
        *,
        datasource_id: str,
        database: str,
        schema: str | None,
        table: str | None,
    ) -> None:
        decision = authorization_service.check_permission(
            access_context,
            "query.execute",
            datasource_id=datasource_id,
            database=database,
            schema=schema,
            table=table,
        )
        if not decision.allowed:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=decision.reason or "Forbidden")

    def _ensure_draft_owner(self, ticket: ChangeTicket, current_user: UserIdentity, access_context: AccessContext) -> None:
        if ticket.submitter_id == current_user.id or "admin" in access_context.roles:
            return
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the submitter can modify this draft")

    def _match_flow(self, db: Session, ticket_type: str) -> ApprovalFlow | None:
        flows = db.execute(
            select(ApprovalFlow)
            .where(ApprovalFlow.enabled.is_(True))
            .order_by(ApprovalFlow.built_in.desc(), ApprovalFlow.version.desc())
        ).scalars().all()
        for flow in flows:
            if flow.ticket_type in {ticket_type, "sql_change"}:
                return flow
            ticket_types = flow.match_rule.get("ticket_types") if isinstance(flow.match_rule, dict) else None
            if isinstance(ticket_types, list) and ticket_type in ticket_types:
                return flow
        return None

    def _build_ticket_number(self) -> str:
        now = datetime.now(UTC)
        return f"TKT-{now:%Y%m%d%H%M%S}-{now.microsecond % 1000000:06d}"

    def _resolve_ticket_type(self, statements: list[ClassifiedStatement]) -> str:
        kinds = {statement.statement_type for statement in statements}
        if len(kinds) == 1:
            return next(iter(kinds))
        return "mixed"

    def _resolve_ticket_risk(self, statements: list[ClassifiedStatement]) -> str:
        if any(statement.risk_level == "high" for statement in statements):
            return "high"
        if any(statement.statement_type == "ddl" for statement in statements):
            return "medium"
        return "low"

    def _summarize_sql(self, statements: list[ClassifiedStatement]) -> str:
        summary_parts = [f"{statement.statement_type.upper()}#{statement.order}" for statement in statements[:5]]
        extra = "" if len(statements) <= 5 else f" +{len(statements) - 5} more"
        return ", ".join(summary_parts) + extra

    def _leading_keyword(self, statement: str) -> str:
        normalized = statement.strip().lstrip("(")
        pieces = normalized.split(None, 1)
        return pieces[0].lower() if pieces else ""

    def _next_waiting_step(self, bundle: TicketBundle, current_step_no: int) -> ApprovalInstanceStep | None:
        for step in bundle.approval_steps:
            if step.step_no > current_step_no and step.status == "waiting":
                return step
        return None
