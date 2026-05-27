from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ApprovalFlowStepResponse(BaseModel):
    id: str
    step_no: int
    step_name: str
    approval_mode: str
    approver_type: str
    approver_ref: str
    rule: dict


class ApprovalFlowResponse(BaseModel):
    id: str
    code: str
    name: str
    description: str | None
    ticket_type: str
    match_rule: dict
    enabled: bool
    built_in: bool
    version: int
    steps: list[ApprovalFlowStepResponse]


class ApprovalFlowStepUpsertRequest(BaseModel):
    step_name: str = Field(min_length=1, max_length=120)
    approval_mode: Literal["any_one", "all"] = "any_one"
    approver_type: Literal["role", "user"]
    approver_ref: str = Field(min_length=1, max_length=512)
    rule: dict = Field(default_factory=dict)


class ApprovalFlowUpsertRequest(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    ticket_type: str = Field(min_length=1, max_length=32)
    match_rule: dict = Field(default_factory=dict)
    enabled: bool = True
    steps: list[ApprovalFlowStepUpsertRequest] = Field(min_length=1)


class ApprovalActionResponse(BaseModel):
    id: str
    actor_user_id: str
    action: str
    comment: str | None
    payload: dict
    created_at: datetime


class ApprovalInstanceStepResponse(BaseModel):
    id: str
    step_no: int
    step_name: str
    approval_mode: str
    approver_type: str
    approver_ref: str
    rule: dict
    status: str
    started_at: datetime | None
    finished_at: datetime | None
    actions: list[ApprovalActionResponse]


class ApprovalInstanceResponse(BaseModel):
    id: str
    flow_id: str
    status: str
    current_step_no: int | None
    started_at: datetime
    finished_at: datetime | None
    steps: list[ApprovalInstanceStepResponse]


class ChangeTicketStatementResponse(BaseModel):
    id: str
    statement_order: int
    statement_text: str
    statement_type: str
    risk_tags: list[str]
    risk_level: str


class ExecutionStatementResultResponse(BaseModel):
    id: str
    statement_order: int
    statement_text: str
    success: bool
    affected_rows: int | None
    duration_ms: int | None
    db_error_code: str | None
    db_error_message: str | None
    result: dict


class ExecutionJobResponse(BaseModel):
    id: str
    run_key: str
    status: str
    executor_type: str
    executor_user_id: str | None
    execution_mode: str
    started_at: datetime | None
    finished_at: datetime | None
    error_message: str | None
    result_summary: dict
    created_at: datetime
    statements: list[ExecutionStatementResultResponse]


class ChangeTicketResponse(BaseModel):
    id: str
    ticket_no: str
    ticket_type: str
    title: str
    datasource_id: str
    target_database: str
    target_schema: str | None
    target_table: str | None
    risk_level: str
    sql_text: str
    sql_summary: str | None
    submitter_id: str
    current_status: str
    scheduled_at: datetime | None
    submitted_at: datetime | None
    approved_at: datetime | None
    executed_at: datetime | None
    created_at: datetime
    updated_at: datetime
    statements: list[ChangeTicketStatementResponse]
    approval_instance: ApprovalInstanceResponse | None
    execution_jobs: list[ExecutionJobResponse]
    available_actions: list[str]


class ApprovalTicketCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    datasource_id: str = Field(min_length=1, max_length=128)
    target_database: str = Field(min_length=1, max_length=128)
    target_schema: str | None = Field(default=None, max_length=128)
    target_table: str | None = Field(default=None, max_length=128)
    sql_text: str = Field(min_length=1)
    scheduled_at: datetime | None = None


class ApprovalDecisionRequest(BaseModel):
    comment: str | None = Field(default=None, max_length=2000)
