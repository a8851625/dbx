from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

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
from app.services.sql_classification import (
    ClassifiedStatement,
    classify_sql_statements,
    resolve_ticket_risk,
    resolve_ticket_type,
    summarize_sql,
)


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


class ApprovalService:
    def sync_builtin_flows(self, db: Session) -> None:
        flow = db.execute(
            select(ApprovalFlow).where(ApprovalFlow.code == "builtin-sql-change-admin")
        ).scalar_one_or_none()
        if flow is None:
            flow = ApprovalFlow(
                code="builtin-sql-change-admin",
                name="Built-in SQL Change Approval",
                description="Default approval template for DDL/DML tickets routed to admin reviewers.",
                ticket_type="sql_change",
                match_rule={"ticket_types": ["ddl", "dml", "mixed"]},
                enabled=True,
                built_in=True,
                version=1,
            )
            db.add(flow)
        else:
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
            steps = self._load_flow_steps(db, flow.id)
            result.append((flow, steps))
        return result

    def create_flow(
        self,
        db: Session,
        *,
        code: str,
        name: str,
        description: str | None,
        ticket_type: str,
        match_rule: dict,
        enabled: bool,
        steps: list[dict],
    ) -> tuple[ApprovalFlow, list[ApprovalFlowStep]]:
        normalized_code = code.strip()
        existing = db.execute(select(ApprovalFlow).where(ApprovalFlow.code == normalized_code)).scalar_one_or_none()
        if existing is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Approval flow code already exists")

        flow = ApprovalFlow(
            code=normalized_code,
            name=name.strip(),
            description=description.strip() if description else None,
            ticket_type=ticket_type.strip(),
            match_rule=match_rule or {},
            enabled=enabled,
            built_in=False,
            version=1,
        )
        db.add(flow)
        db.flush()
        created_steps = self._replace_flow_steps(db, flow, steps)
        db.commit()
        db.refresh(flow)
        return flow, created_steps

    def update_flow(
        self,
        db: Session,
        flow_id: str,
        *,
        code: str,
        name: str,
        description: str | None,
        ticket_type: str,
        match_rule: dict,
        enabled: bool,
        steps: list[dict],
    ) -> tuple[ApprovalFlow, list[ApprovalFlowStep]]:
        flow = db.get(ApprovalFlow, flow_id)
        if flow is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Approval flow not found")
        if flow.built_in:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Built-in approval flow cannot be edited")

        normalized_code = code.strip()
        existing = db.execute(
            select(ApprovalFlow).where(ApprovalFlow.code == normalized_code, ApprovalFlow.id != flow_id)
        ).scalar_one_or_none()
        if existing is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Approval flow code already exists")

        flow.code = normalized_code
        flow.name = name.strip()
        flow.description = description.strip() if description else None
        flow.ticket_type = ticket_type.strip()
        flow.match_rule = match_rule or {}
        flow.enabled = enabled
        flow.version += 1
        replaced_steps = self._replace_flow_steps(db, flow, steps)
        db.commit()
        db.refresh(flow)
        return flow, replaced_steps

    def delete_flow(self, db: Session, flow_id: str) -> None:
        flow = db.get(ApprovalFlow, flow_id)
        if flow is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Approval flow not found")
        if flow.built_in:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Built-in approval flow cannot be deleted")

        db.delete(flow)
        db.commit()

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
            ticket_type=resolve_ticket_type(classified_statements),
            title=title.strip(),
            datasource_id=datasource_id.strip(),
            target_database=target_database.strip(),
            target_schema=target_schema.strip() if target_schema else None,
            target_table=target_table.strip() if target_table else None,
            risk_level=resolve_ticket_risk(classified_statements),
            sql_text=sql_text.strip(),
            sql_summary=summarize_sql(classified_statements),
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

        flow = self._match_flow(db, ticket.ticket_type, ticket)
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
        try:
            classified = classify_sql_statements(sql_text)
        except ValueError:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="SQL payload is empty")
        for statement in classified:
            if statement.statement_type not in {"ddl", "dml"}:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Unsupported statement type in ticket: {statement.keyword or 'unknown'}",
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

    def _match_flow(self, db: Session, ticket_type: str, ticket: ChangeTicket | None = None) -> ApprovalFlow | None:
        flows = db.execute(
            select(ApprovalFlow)
            .where(ApprovalFlow.enabled.is_(True))
            .order_by(ApprovalFlow.built_in.asc(), ApprovalFlow.version.desc(), ApprovalFlow.code.asc())
        ).scalars().all()
        for flow in flows:
            if self._flow_matches_ticket(flow, ticket_type, ticket):
                return flow
        return None

    def _flow_matches_ticket(self, flow: ApprovalFlow, ticket_type: str, ticket: ChangeTicket | None) -> bool:
        match_rule = flow.match_rule if isinstance(flow.match_rule, dict) else {}
        ticket_types = match_rule.get("ticket_types")
        if isinstance(ticket_types, list) and ticket_types:
            if ticket_type not in {str(item).strip() for item in ticket_types if str(item).strip()}:
                return False
        elif flow.ticket_type not in {ticket_type, "sql_change"}:
            return False

        if ticket is None:
            return True

        if not self._matches_rule_values(match_rule.get("datasource_ids"), ticket.datasource_id):
            return False
        if not self._matches_rule_values(match_rule.get("databases"), ticket.target_database):
            return False
        if not self._matches_rule_values(match_rule.get("schemas"), ticket.target_schema):
            return False
        if not self._matches_rule_values(match_rule.get("tables"), ticket.target_table):
            return False
        if not self._matches_rule_values(match_rule.get("risk_levels"), ticket.risk_level):
            return False
        return True

    def _matches_rule_values(self, expected: object, actual: str | None) -> bool:
        if not isinstance(expected, list) or not expected:
            return True
        if actual is None:
            return False
        normalized = {str(item).strip() for item in expected if str(item).strip()}
        return actual in normalized

    def _build_ticket_number(self) -> str:
        now = datetime.now(UTC)
        return f"TKT-{now:%Y%m%d%H%M%S}-{now.microsecond % 1000000:06d}"

    def _load_flow_steps(self, db: Session, flow_id: str) -> list[ApprovalFlowStep]:
        return db.execute(
            select(ApprovalFlowStep)
            .where(ApprovalFlowStep.flow_id == flow_id)
            .order_by(ApprovalFlowStep.step_no.asc())
        ).scalars().all()

    def _replace_flow_steps(self, db: Session, flow: ApprovalFlow, steps: list[dict]) -> list[ApprovalFlowStep]:
        for step in self._load_flow_steps(db, flow.id):
            db.delete(step)
        db.flush()

        created_steps: list[ApprovalFlowStep] = []
        for index, step in enumerate(steps, start=1):
            created = ApprovalFlowStep(
                id=str(uuid4()),
                flow_id=flow.id,
                step_no=index,
                step_name=str(step["step_name"]).strip(),
                approval_mode=str(step["approval_mode"]).strip(),
                approver_type=str(step["approver_type"]).strip(),
                approver_ref=str(step["approver_ref"]).strip(),
                rule=step.get("rule") or {},
            )
            db.add(created)
            created_steps.append(created)
        db.flush()
        return created_steps

    def _next_waiting_step(self, bundle: TicketBundle, current_step_no: int) -> ApprovalInstanceStep | None:
        for step in bundle.approval_steps:
            if step.step_no > current_step_no and step.status == "waiting":
                return step
        return None
