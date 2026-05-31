from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AuditEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    event_type: str
    category: str
    action: str
    outcome: str
    actor_user_id: str | None
    actor_email: str | None
    actor_display_name: str | None
    actor_role: str | None
    source_ip: str | None
    user_agent: str | None
    request_id: str | None
    trace_id: str | None
    request_path: str | None
    request_method: str | None
    resource_type: str | None
    resource_id: str | None
    resource_name: str | None
    payload: dict[str, Any]
    created_at: datetime


class QueryAuditResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    execution_id: str | None
    actor_user_id: str | None
    actor_email: str | None
    actor_display_name: str | None
    actor_role: str | None
    source_ip: str | None
    user_agent: str | None
    request_id: str | None
    trace_id: str | None
    request_path: str | None
    request_method: str | None
    datasource_id: str
    database_name: str
    schema_name: str | None
    table_name: str | None
    operation_type: str
    execution_mode: str
    statement_count: int
    sql_text: str
    sql_summary: str | None
    status: str
    duration_ms: int | None
    affected_rows: int | None
    error_code: str | None
    error_message: str | None
    metadata: dict[str, Any]
    created_at: datetime
    completed_at: datetime | None


class AuditEventListResponse(BaseModel):
    items: list[AuditEventResponse]
    total: int


class QueryAuditListResponse(BaseModel):
    items: list[QueryAuditResponse]
    total: int


class InternalQueryAuditCreateRequest(BaseModel):
    session_token: str | None = None
    execution_id: str | None = None
    datasource_id: str
    database_name: str = Field(alias="database")
    schema_name: str | None = Field(default=None, alias="schema")
    table_name: str | None = Field(default=None, alias="table")
    operation_type: str = "interactive"
    execution_mode: str = "immediate"
    statement_count: int = 1
    sql_text: str
    status: str
    duration_ms: int | None = None
    affected_rows: int | None = None
    error_code: str | None = None
    error_message: str | None = None
    source_ip: str | None = None
    user_agent: str | None = None
    request_id: str | None = None
    trace_id: str | None = None
    request_path: str | None = None
    request_method: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(populate_by_name=True)
