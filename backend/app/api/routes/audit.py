from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from sqlalchemy.orm import Session

from app.api.deps import get_auth_service, get_current_user, require_permission
from app.config import Settings, get_settings
from app.db.session import get_db
from app.models.auth import UserIdentity
from app.schemas.audit import (
    AuditEventListResponse,
    AuditEventResponse,
    InternalQueryAuditCreateRequest,
    QueryAuditListResponse,
    QueryAuditResponse,
)
from app.services.audit import AuditService
from app.services.auth import AuthService

router = APIRouter(prefix="/audit", tags=["audit"])


def get_audit_service() -> AuditService:
    return AuditService()


def require_internal_token(
    x_dbx_internal_token: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    expected = settings.enterprise_internal_token.strip()
    if not expected or x_dbx_internal_token != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid internal token")


@router.get("/events", response_model=AuditEventListResponse)
def list_audit_events(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    category: str | None = Query(default=None),
    action: str | None = Query(default=None),
    outcome: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    resource_type: str | None = Query(default=None),
    keyword: str | None = Query(default=None),
    _: UserIdentity = Depends(require_permission("audit.event.view")),
    db: Session = Depends(get_db),
    audit_service: AuditService = Depends(get_audit_service),
) -> AuditEventListResponse:
    items, total = audit_service.list_events(
        db,
        limit=limit,
        offset=offset,
        category=category,
        action=action,
        outcome=outcome,
        actor=actor,
        resource_type=resource_type,
        keyword=keyword,
    )
    return AuditEventListResponse(items=[AuditEventResponse.model_validate(item) for item in items], total=total)


@router.get("/queries", response_model=QueryAuditListResponse)
def list_query_audits(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    datasource_id: str | None = Query(default=None),
    database_name: str | None = Query(default=None),
    status_value: str | None = Query(default=None, alias="status"),
    operation_type: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    keyword: str | None = Query(default=None),
    _: UserIdentity = Depends(require_permission("audit.event.view")),
    db: Session = Depends(get_db),
    audit_service: AuditService = Depends(get_audit_service),
) -> QueryAuditListResponse:
    items, total = audit_service.list_queries(
        db,
        limit=limit,
        offset=offset,
        datasource_id=datasource_id,
        database_name=database_name,
        status=status_value,
        operation_type=operation_type,
        actor=actor,
        keyword=keyword,
    )
    return QueryAuditListResponse(items=[_serialize_query_audit(item) for item in items], total=total)


@router.post("/internal/query", status_code=status.HTTP_202_ACCEPTED)
def ingest_internal_query_audit(
    payload: InternalQueryAuditCreateRequest,
    _: None = Depends(require_internal_token),
    db: Session = Depends(get_db),
    auth_service: AuthService = Depends(get_auth_service),
    audit_service: AuditService = Depends(get_audit_service),
) -> Response:
    context = auth_service.read_session(db, payload.session_token) if payload.session_token else None
    actor = audit_service.build_actor(
        user=context.user if context is not None else None,
        source_ip=payload.source_ip,
        user_agent=payload.user_agent,
        request_path=payload.request_path,
        request_method=payload.request_method,
    )
    audit_service.record_query(
        db,
        datasource_id=payload.datasource_id,
        database_name=payload.database_name,
        schema_name=payload.schema_name,
        table_name=payload.table_name,
        actor=actor,
        execution_id=payload.execution_id,
        operation_type=payload.operation_type,
        execution_mode=payload.execution_mode,
        statement_count=payload.statement_count,
        sql_text=payload.sql_text,
        status=payload.status,
        duration_ms=payload.duration_ms,
        affected_rows=payload.affected_rows,
        error_code=payload.error_code,
        error_message=payload.error_message,
        metadata=payload.metadata,
        completed_at=datetime.now(UTC),
    )
    db.commit()
    return Response(status_code=status.HTTP_202_ACCEPTED)


def _serialize_query_audit(item) -> QueryAuditResponse:
    return QueryAuditResponse(
        id=item.id,
        execution_id=item.execution_id,
        actor_user_id=item.actor_user_id,
        actor_email=item.actor_email,
        actor_display_name=item.actor_display_name,
        actor_role=item.actor_role,
        source_ip=item.source_ip,
        user_agent=item.user_agent,
        request_path=item.request_path,
        request_method=item.request_method,
        datasource_id=item.datasource_id,
        database_name=item.database_name,
        schema_name=item.schema_name,
        table_name=item.table_name,
        operation_type=item.operation_type,
        execution_mode=item.execution_mode,
        statement_count=item.statement_count,
        sql_text=item.sql_text,
        sql_summary=item.sql_summary,
        status=item.status,
        duration_ms=item.duration_ms,
        affected_rows=item.affected_rows,
        error_code=item.error_code,
        error_message=item.error_message,
        metadata=item.details or {},
        created_at=item.created_at,
        completed_at=item.completed_at,
    )
