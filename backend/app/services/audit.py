from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fastapi import Request
from sqlalchemy import String, Text, cast, func, or_, select
from sqlalchemy.orm import Session

from app.models.audit import AuditEvent, QueryAudit
from app.models.auth import UserIdentity
from app.services.connection_secrets import SECRET_PLACEHOLDER, SENSITIVE_CONNECTION_FIELDS

MAX_SQL_TEXT_LENGTH = 20_000
MAX_SQL_SUMMARY_LENGTH = 240


@dataclass(frozen=True)
class AuditActor:
    user_id: str | None = None
    email: str | None = None
    display_name: str | None = None
    role: str | None = None
    source_ip: str | None = None
    user_agent: str | None = None
    request_id: str | None = None
    trace_id: str | None = None
    request_path: str | None = None
    request_method: str | None = None


class AuditService:
    def build_actor(
        self,
        *,
        user: UserIdentity | None = None,
        request: Request | None = None,
        source_ip: str | None = None,
        user_agent: str | None = None,
        request_id: str | None = None,
        trace_id: str | None = None,
        request_path: str | None = None,
        request_method: str | None = None,
    ) -> AuditActor:
        request_state = getattr(request, "state", None)
        return AuditActor(
            user_id=user.id if user is not None else None,
            email=user.email if user is not None else None,
            display_name=user.display_name if user is not None else None,
            role=user.role if user is not None else None,
            source_ip=source_ip or self._source_ip(request),
            user_agent=user_agent or (request.headers.get("user-agent") if request is not None else None),
            request_id=request_id or getattr(request_state, "request_id", None),
            trace_id=trace_id or getattr(request_state, "trace_id", None),
            request_path=request_path or (str(request.url.path) if request is not None else None),
            request_method=request_method or (request.method if request is not None else None),
        )

    def record_event(
        self,
        db: Session,
        *,
        event_type: str,
        category: str,
        action: str,
        outcome: str = "success",
        actor: AuditActor | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        resource_name: str | None = None,
        payload: dict[str, Any] | None = None,
        commit: bool = False,
    ) -> AuditEvent:
        actor = actor or AuditActor()
        event = AuditEvent(
            event_type=event_type,
            category=category,
            action=action,
            outcome=outcome,
            actor_user_id=actor.user_id,
            actor_email=actor.email,
            actor_display_name=actor.display_name,
            actor_role=actor.role,
            source_ip=actor.source_ip,
            user_agent=actor.user_agent,
            request_id=actor.request_id,
            trace_id=actor.trace_id,
            request_path=actor.request_path,
            request_method=actor.request_method,
            resource_type=resource_type,
            resource_id=resource_id,
            resource_name=resource_name,
            payload=self._redact_sensitive_payload(payload or {}),
        )
        db.add(event)
        if commit:
            db.commit()
            db.refresh(event)
        return event

    def record_query(
        self,
        db: Session,
        *,
        datasource_id: str,
        database_name: str,
        sql_text: str | None = None,
        actor: AuditActor | None = None,
        schema_name: str | None = None,
        table_name: str | None = None,
        execution_id: str | None = None,
        operation_type: str = "interactive",
        execution_mode: str = "immediate",
        statement_count: int = 1,
        status: str = "succeeded",
        duration_ms: int | None = None,
        affected_rows: int | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        metadata: dict[str, Any] | None = None,
        completed_at: datetime | None = None,
        commit: bool = False,
    ) -> QueryAudit:
        actor = actor or AuditActor()
        query = QueryAudit(
            execution_id=execution_id,
            actor_user_id=actor.user_id,
            actor_email=actor.email,
            actor_display_name=actor.display_name,
            actor_role=actor.role,
            source_ip=actor.source_ip,
            user_agent=actor.user_agent,
            request_id=actor.request_id,
            trace_id=actor.trace_id,
            request_path=actor.request_path,
            request_method=actor.request_method,
            datasource_id=datasource_id,
            database_name=database_name,
            schema_name=schema_name,
            table_name=table_name,
            operation_type=operation_type,
            execution_mode=execution_mode,
            statement_count=max(statement_count, 1),
            sql_text=self._trim_sql(sql_text or ""),
            sql_summary=self._summarize_sql(sql_text or ""),
            status=status,
            duration_ms=duration_ms,
            affected_rows=affected_rows,
            error_code=error_code,
            error_message=error_message,
            details=self._redact_sensitive_payload(metadata or {}),
            completed_at=completed_at or datetime.now(UTC),
        )
        db.add(query)
        if commit:
            db.commit()
            db.refresh(query)
        return query

    def list_events(
        self,
        db: Session,
        *,
        limit: int,
        offset: int,
        category: str | None = None,
        action: str | None = None,
        outcome: str | None = None,
        actor: str | None = None,
        resource_type: str | None = None,
        request_id: str | None = None,
        trace_id: str | None = None,
        keyword: str | None = None,
    ) -> tuple[list[AuditEvent], int]:
        stmt = select(AuditEvent)
        count_stmt = select(func.count()).select_from(AuditEvent)

        stmt = self._apply_event_filters(
            stmt,
            category=category,
            action=action,
            outcome=outcome,
            actor=actor,
            resource_type=resource_type,
            request_id=request_id,
            trace_id=trace_id,
            keyword=keyword,
        )
        count_stmt = self._apply_event_filters(
            count_stmt,
            category=category,
            action=action,
            outcome=outcome,
            actor=actor,
            resource_type=resource_type,
            request_id=request_id,
            trace_id=trace_id,
            keyword=keyword,
        )

        total = int(db.execute(count_stmt).scalar_one())
        items = db.execute(
            stmt.order_by(AuditEvent.created_at.desc()).offset(offset).limit(limit)
        ).scalars().all()
        return items, total

    def list_queries(
        self,
        db: Session,
        *,
        limit: int,
        offset: int,
        datasource_id: str | None = None,
        database_name: str | None = None,
        status: str | None = None,
        operation_type: str | None = None,
        actor: str | None = None,
        request_id: str | None = None,
        trace_id: str | None = None,
        keyword: str | None = None,
    ) -> tuple[list[QueryAudit], int]:
        stmt = select(QueryAudit)
        count_stmt = select(func.count()).select_from(QueryAudit)

        stmt = self._apply_query_filters(
            stmt,
            datasource_id=datasource_id,
            database_name=database_name,
            status=status,
            operation_type=operation_type,
            actor=actor,
            request_id=request_id,
            trace_id=trace_id,
            keyword=keyword,
        )
        count_stmt = self._apply_query_filters(
            count_stmt,
            datasource_id=datasource_id,
            database_name=database_name,
            status=status,
            operation_type=operation_type,
            actor=actor,
            request_id=request_id,
            trace_id=trace_id,
            keyword=keyword,
        )

        total = int(db.execute(count_stmt).scalar_one())
        items = db.execute(
            stmt.order_by(QueryAudit.created_at.desc()).offset(offset).limit(limit)
        ).scalars().all()
        return items, total

    def _apply_event_filters(
        self,
        stmt,
        *,
        category: str | None,
        action: str | None,
        outcome: str | None,
        actor: str | None,
        resource_type: str | None,
        request_id: str | None,
        trace_id: str | None,
        keyword: str | None,
    ):
        if category:
            stmt = stmt.where(AuditEvent.category == category)
        if action:
            stmt = stmt.where(AuditEvent.action == action)
        if outcome:
            stmt = stmt.where(AuditEvent.outcome == outcome)
        if actor:
            actor_like = f"%{actor.strip()}%"
            stmt = stmt.where(
                or_(
                    AuditEvent.actor_email.ilike(actor_like),
                    AuditEvent.actor_display_name.ilike(actor_like),
                    AuditEvent.actor_user_id.ilike(actor_like),
                )
            )
        if resource_type:
            stmt = stmt.where(AuditEvent.resource_type == resource_type)
        if request_id:
            stmt = stmt.where(AuditEvent.request_id == request_id.strip())
        if trace_id:
            stmt = stmt.where(AuditEvent.trace_id == trace_id.strip())
        if keyword:
            like = f"%{keyword.strip()}%"
            stmt = stmt.where(
                or_(
                    AuditEvent.event_type.ilike(like),
                    AuditEvent.resource_name.ilike(like),
                    AuditEvent.resource_id.ilike(like),
                    cast(AuditEvent.payload, Text).ilike(like),
                )
            )
        return stmt

    def _apply_query_filters(
        self,
        stmt,
        *,
        datasource_id: str | None,
        database_name: str | None,
        status: str | None,
        operation_type: str | None,
        actor: str | None,
        request_id: str | None,
        trace_id: str | None,
        keyword: str | None,
    ):
        if datasource_id:
            stmt = stmt.where(QueryAudit.datasource_id == datasource_id)
        if database_name:
            stmt = stmt.where(QueryAudit.database_name == database_name)
        if status:
            stmt = stmt.where(QueryAudit.status == status)
        if operation_type:
            stmt = stmt.where(QueryAudit.operation_type == operation_type)
        if actor:
            actor_like = f"%{actor.strip()}%"
            stmt = stmt.where(
                or_(
                    QueryAudit.actor_email.ilike(actor_like),
                    QueryAudit.actor_display_name.ilike(actor_like),
                    QueryAudit.actor_user_id.ilike(actor_like),
                )
            )
        if request_id:
            stmt = stmt.where(QueryAudit.request_id == request_id.strip())
        if trace_id:
            stmt = stmt.where(QueryAudit.trace_id == trace_id.strip())
        if keyword:
            like = f"%{keyword.strip()}%"
            stmt = stmt.where(
                or_(
                    QueryAudit.sql_summary.ilike(like),
                    QueryAudit.sql_text.ilike(like),
                    QueryAudit.datasource_id.ilike(like),
                    QueryAudit.database_name.ilike(like),
                    cast(QueryAudit.details, Text).ilike(like),
                )
            )
        return stmt

    def _trim_sql(self, sql_text: str) -> str:
        text = sql_text.strip()
        if len(text) <= MAX_SQL_TEXT_LENGTH:
            return text
        return text[: MAX_SQL_TEXT_LENGTH - 12] + "\n/* truncated */"

    def _summarize_sql(self, sql_text: str) -> str:
        normalized = " ".join(part for part in sql_text.strip().split())
        if len(normalized) <= MAX_SQL_SUMMARY_LENGTH:
            return normalized
        return normalized[: MAX_SQL_SUMMARY_LENGTH - 3] + "..."

    def _redact_sensitive_payload(self, value: Any) -> Any:
        if isinstance(value, dict):
            redacted: dict[str, Any] = {}
            for key, item in value.items():
                if self._is_sensitive_key(str(key)):
                    redacted[str(key)] = SECRET_PLACEHOLDER
                else:
                    redacted[str(key)] = self._redact_sensitive_payload(item)
            return redacted
        if isinstance(value, list):
            return [self._redact_sensitive_payload(item) for item in value]
        return value

    def _is_sensitive_key(self, key: str) -> bool:
        normalized = key.lower().replace("-", "_")
        return (
            normalized in SENSITIVE_CONNECTION_FIELDS
            or normalized.endswith("secret")
            or normalized.endswith("secrets")
            or normalized.endswith("_token")
            or "password" in normalized
            or "passphrase" in normalized
            or "credential" in normalized
        )

    def _source_ip(self, request: Request | None) -> str | None:
        if request is None:
            return None
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip() or None
        if request.client is None:
            return None
        return request.client.host
