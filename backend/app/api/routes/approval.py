from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_authorization_service, get_current_user, require_permission
from app.db.session import get_db
from app.models.approval import ApprovalAction, ApprovalFlow, ApprovalFlowStep, ApprovalInstanceStep, ExecutionJob
from app.models.auth import UserIdentity
from app.schemas.approval import (
    ApprovalActionResponse,
    ApprovalDecisionRequest,
    ApprovalFlowResponse,
    ApprovalFlowStepResponse,
    ApprovalInstanceResponse,
    ApprovalInstanceStepResponse,
    ApprovalTicketCreateRequest,
    ChangeTicketResponse,
    ChangeTicketStatementResponse,
    ExecutionJobResponse,
    ExecutionStatementResultResponse,
)
from app.services.approval import ApprovalService, TicketBundle
from app.services.authorization import AccessContext, AuthorizationService
from app.services.execution import ExecutionService

router = APIRouter(prefix="/approval", tags=["approval"])


def get_approval_service() -> ApprovalService:
    return ApprovalService()


def get_execution_service() -> ExecutionService:
    return ExecutionService()


@router.get("/flows", response_model=list[ApprovalFlowResponse])
def list_approval_flows(
    _: UserIdentity = Depends(require_permission("approval.ticket.view")),
    db: Session = Depends(get_db),
    approval_service: ApprovalService = Depends(get_approval_service),
) -> list[ApprovalFlowResponse]:
    return [_serialize_flow(flow, steps) for flow, steps in approval_service.list_flows(db)]


@router.get("/tickets", response_model=list[ChangeTicketResponse])
def list_approval_tickets(
    scope: Literal["my", "pending", "all"] = Query(default="my"),
    current_user: UserIdentity = Depends(require_permission("approval.ticket.view")),
    db: Session = Depends(get_db),
    authorization_service: AuthorizationService = Depends(get_authorization_service),
    approval_service: ApprovalService = Depends(get_approval_service),
) -> list[ChangeTicketResponse]:
    access_context = authorization_service.build_access_context(db, current_user)
    tickets = approval_service.list_visible_tickets(db, current_user, access_context, scope=scope)
    return [
        _serialize_bundle(approval_service, approval_service.load_ticket_bundle(db, ticket.id), current_user, access_context)
        for ticket in tickets
    ]


@router.get("/tickets/{ticket_id}", response_model=ChangeTicketResponse)
def get_approval_ticket(
    ticket_id: str,
    current_user: UserIdentity = Depends(require_permission("approval.ticket.view")),
    db: Session = Depends(get_db),
    authorization_service: AuthorizationService = Depends(get_authorization_service),
    approval_service: ApprovalService = Depends(get_approval_service),
) -> ChangeTicketResponse:
    access_context = authorization_service.build_access_context(db, current_user)
    bundle = approval_service.get_visible_bundle(db, ticket_id, current_user, access_context)
    return _serialize_bundle(approval_service, bundle, current_user, access_context)


@router.post("/tickets", response_model=ChangeTicketResponse)
def create_approval_ticket(
    payload: ApprovalTicketCreateRequest,
    current_user: UserIdentity = Depends(require_permission("approval.ticket.create")),
    db: Session = Depends(get_db),
    authorization_service: AuthorizationService = Depends(get_authorization_service),
    approval_service: ApprovalService = Depends(get_approval_service),
) -> ChangeTicketResponse:
    access_context = authorization_service.build_access_context(db, current_user)
    ticket = approval_service.create_ticket(
        db,
        current_user,
        access_context,
        authorization_service,
        title=payload.title,
        datasource_id=payload.datasource_id,
        target_database=payload.target_database,
        target_schema=payload.target_schema,
        target_table=payload.target_table,
        sql_text=payload.sql_text,
        scheduled_at=payload.scheduled_at,
    )
    bundle = approval_service.load_ticket_bundle(db, ticket.id)
    return _serialize_bundle(approval_service, bundle, current_user, access_context)


@router.post("/tickets/{ticket_id}/submit", response_model=ChangeTicketResponse)
def submit_approval_ticket(
    ticket_id: str,
    current_user: UserIdentity = Depends(require_permission("approval.ticket.submit")),
    db: Session = Depends(get_db),
    authorization_service: AuthorizationService = Depends(get_authorization_service),
    approval_service: ApprovalService = Depends(get_approval_service),
) -> ChangeTicketResponse:
    access_context = authorization_service.build_access_context(db, current_user)
    transition = approval_service.submit_ticket(db, ticket_id, current_user, access_context, authorization_service)
    bundle = approval_service.get_visible_bundle(db, transition.ticket.id, current_user, access_context)
    return _serialize_bundle(approval_service, bundle, current_user, access_context)


@router.post("/tickets/{ticket_id}/approve", response_model=ChangeTicketResponse)
def approve_approval_ticket(
    ticket_id: str,
    payload: ApprovalDecisionRequest,
    background_tasks: BackgroundTasks,
    current_user: UserIdentity = Depends(require_permission("approval.ticket.approve")),
    db: Session = Depends(get_db),
    authorization_service: AuthorizationService = Depends(get_authorization_service),
    approval_service: ApprovalService = Depends(get_approval_service),
    execution_service: ExecutionService = Depends(get_execution_service),
) -> ChangeTicketResponse:
    access_context = authorization_service.build_access_context(db, current_user)
    transition = approval_service.approve_ticket(
        db,
        ticket_id,
        current_user,
        access_context,
        comment=payload.comment,
    )
    if transition.should_queue_execution:
        job = execution_service.ensure_job_for_ticket(
            db,
            transition.ticket,
            run_key=f"approval:{transition.ticket.latest_flow_instance_id or transition.ticket.id}",
        )
        db.commit()
        background_tasks.add_task(execution_service.run_job, job.id)
    bundle = approval_service.get_visible_bundle(db, transition.ticket.id, current_user, access_context)
    return _serialize_bundle(approval_service, bundle, current_user, access_context)


@router.post("/tickets/{ticket_id}/reject", response_model=ChangeTicketResponse)
def reject_approval_ticket(
    ticket_id: str,
    payload: ApprovalDecisionRequest,
    current_user: UserIdentity = Depends(require_permission("approval.ticket.approve")),
    db: Session = Depends(get_db),
    authorization_service: AuthorizationService = Depends(get_authorization_service),
    approval_service: ApprovalService = Depends(get_approval_service),
) -> ChangeTicketResponse:
    access_context = authorization_service.build_access_context(db, current_user)
    transition = approval_service.reject_ticket(
        db,
        ticket_id,
        current_user,
        access_context,
        comment=payload.comment,
    )
    bundle = approval_service.get_visible_bundle(db, transition.ticket.id, current_user, access_context)
    return _serialize_bundle(approval_service, bundle, current_user, access_context)


@router.post("/tickets/{ticket_id}/retry", response_model=ChangeTicketResponse)
def retry_approval_ticket(
    ticket_id: str,
    background_tasks: BackgroundTasks,
    current_user: UserIdentity = Depends(require_permission("approval.ticket.execute")),
    db: Session = Depends(get_db),
    authorization_service: AuthorizationService = Depends(get_authorization_service),
    approval_service: ApprovalService = Depends(get_approval_service),
    execution_service: ExecutionService = Depends(get_execution_service),
) -> ChangeTicketResponse:
    access_context = authorization_service.build_access_context(db, current_user)
    bundle = approval_service.get_visible_bundle(db, ticket_id, current_user, access_context)
    if bundle.ticket.current_status not in {"approved", "failed"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only approved or failed tickets can be retried",
        )
    job = execution_service.ensure_job_for_ticket(
        db,
        bundle.ticket,
        run_key=f"manual:{datetime.now(UTC).isoformat()}",
        executor_user_id=current_user.id,
    )
    db.commit()
    background_tasks.add_task(execution_service.run_job, job.id)
    refreshed = approval_service.get_visible_bundle(db, ticket_id, current_user, access_context)
    return _serialize_bundle(approval_service, refreshed, current_user, access_context)


def _serialize_flow(flow: ApprovalFlow, steps: list[ApprovalFlowStep]) -> ApprovalFlowResponse:
    return ApprovalFlowResponse(
        id=flow.id,
        code=flow.code,
        name=flow.name,
        description=flow.description,
        ticket_type=flow.ticket_type,
        match_rule=flow.match_rule or {},
        enabled=flow.enabled,
        built_in=flow.built_in,
        version=flow.version,
        steps=[
            ApprovalFlowStepResponse(
                id=step.id,
                step_no=step.step_no,
                step_name=step.step_name,
                approval_mode=step.approval_mode,
                approver_type=step.approver_type,
                approver_ref=step.approver_ref,
                rule=step.rule or {},
            )
            for step in steps
        ],
    )


def _serialize_bundle(
    approval_service: ApprovalService,
    bundle: TicketBundle,
    current_user: UserIdentity,
    access_context: AccessContext,
) -> ChangeTicketResponse:
    approval_instance = None
    if bundle.approval_instance is not None:
        approval_instance = ApprovalInstanceResponse(
            id=bundle.approval_instance.id,
            flow_id=bundle.approval_instance.flow_id,
            status=bundle.approval_instance.status,
            current_step_no=bundle.approval_instance.current_step_no,
            started_at=bundle.approval_instance.started_at,
            finished_at=bundle.approval_instance.finished_at,
            steps=[
                _serialize_instance_step(step, bundle.approval_actions.get(step.id, []))
                for step in bundle.approval_steps
            ],
        )

    execution_jobs = [
        _serialize_execution_job(job, bundle.execution_results.get(job.id, []))
        for job in bundle.execution_jobs
    ]

    return ChangeTicketResponse(
        id=bundle.ticket.id,
        ticket_no=bundle.ticket.ticket_no,
        ticket_type=bundle.ticket.ticket_type,
        title=bundle.ticket.title,
        datasource_id=bundle.ticket.datasource_id,
        target_database=bundle.ticket.target_database,
        target_schema=bundle.ticket.target_schema,
        target_table=bundle.ticket.target_table,
        risk_level=bundle.ticket.risk_level,
        sql_text=bundle.ticket.sql_text,
        sql_summary=bundle.ticket.sql_summary,
        submitter_id=bundle.ticket.submitter_id,
        current_status=bundle.ticket.current_status,
        scheduled_at=bundle.ticket.scheduled_at,
        submitted_at=bundle.ticket.submitted_at,
        approved_at=bundle.ticket.approved_at,
        executed_at=bundle.ticket.executed_at,
        created_at=bundle.ticket.created_at,
        updated_at=bundle.ticket.updated_at,
        statements=[
            ChangeTicketStatementResponse(
                id=statement.id,
                statement_order=statement.statement_order,
                statement_text=statement.statement_text,
                statement_type=statement.statement_type,
                risk_tags=[str(item) for item in statement.risk_tags or []],
                risk_level=statement.risk_level,
            )
            for statement in bundle.statements
        ],
        approval_instance=approval_instance,
        execution_jobs=execution_jobs,
        available_actions=approval_service.available_actions(bundle, current_user, access_context),
    )


def _serialize_instance_step(
    step: ApprovalInstanceStep,
    actions: list[ApprovalAction],
) -> ApprovalInstanceStepResponse:
    return ApprovalInstanceStepResponse(
        id=step.id,
        step_no=step.step_no,
        step_name=step.step_name,
        approval_mode=step.approval_mode,
        approver_type=step.approver_type,
        approver_ref=step.approver_ref,
        rule=step.rule or {},
        status=step.status,
        started_at=step.started_at,
        finished_at=step.finished_at,
        actions=[
            ApprovalActionResponse(
                id=action.id,
                actor_user_id=action.actor_user_id,
                action=action.action,
                comment=action.comment,
                payload=action.payload or {},
                created_at=action.created_at,
            )
            for action in actions
        ],
    )


def _serialize_execution_job(
    job: ExecutionJob,
    statements: list,
) -> ExecutionJobResponse:
    return ExecutionJobResponse(
        id=job.id,
        run_key=job.run_key,
        status=job.status,
        executor_type=job.executor_type,
        executor_user_id=job.executor_user_id,
        execution_mode=job.execution_mode,
        started_at=job.started_at,
        finished_at=job.finished_at,
        error_message=job.error_message,
        result_summary=job.result_summary or {},
        created_at=job.created_at,
        statements=[
            ExecutionStatementResultResponse(
                id=statement.id,
                statement_order=statement.statement_order,
                statement_text=statement.statement_text,
                success=statement.success,
                affected_rows=statement.affected_rows,
                duration_ms=statement.duration_ms,
                db_error_code=statement.db_error_code,
                db_error_message=statement.db_error_message,
                result=statement.result or {},
            )
            for statement in statements
        ],
    )


async def run_due_ticket_scheduler(stop_event: asyncio.Event) -> None:
    execution_service = ExecutionService()
    interval = execution_service.settings.approval_scheduler_interval_seconds
    while not stop_event.is_set():
        db = SessionLocal()
        try:
            queued_job_ids = execution_service.enqueue_due_tickets(db)
        finally:
            db.close()

        for job_id in queued_job_ids:
            asyncio.create_task(execution_service.run_job(job_id))

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except TimeoutError:
            continue


async def stop_due_ticket_scheduler(stop_event: asyncio.Event, scheduler_task: asyncio.Task[None]) -> None:
    stop_event.set()
    scheduler_task.cancel()
    with suppress(asyncio.CancelledError):
        await scheduler_task
