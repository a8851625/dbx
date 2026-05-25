from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ApprovalFlow(Base):
    __tablename__ = "approval_flow"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    ticket_type: Mapped[str] = mapped_column(String(32), nullable=False)
    match_rule: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    built_in: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ApprovalFlowStep(Base):
    __tablename__ = "approval_flow_step"
    __table_args__ = (
        UniqueConstraint("flow_id", "step_no", name="uq_approval_flow_step_order"),
    )

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    flow_id: Mapped[str] = mapped_column(ForeignKey("approval_flow.id", ondelete="CASCADE"), nullable=False)
    step_no: Mapped[int] = mapped_column(Integer, nullable=False)
    step_name: Mapped[str] = mapped_column(String(120), nullable=False)
    approval_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="any_one", server_default="any_one")
    approver_type: Mapped[str] = mapped_column(String(16), nullable=False)
    approver_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    rule: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ChangeTicket(Base):
    __tablename__ = "change_ticket"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    ticket_no: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    ticket_type: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    datasource_id: Mapped[str] = mapped_column(String(128), nullable=False)
    target_database: Mapped[str] = mapped_column(String(128), nullable=False)
    target_schema: Mapped[str | None] = mapped_column(String(128), nullable=True)
    target_table: Mapped[str | None] = mapped_column(String(128), nullable=True)
    risk_level: Mapped[str] = mapped_column(String(32), nullable=False, default="medium", server_default="medium")
    sql_text: Mapped[str] = mapped_column(Text, nullable=False)
    sql_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    submitter_id: Mapped[str] = mapped_column(ForeignKey("user_identity.id", ondelete="RESTRICT"), nullable=False)
    current_status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft", server_default="draft")
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    latest_flow_instance_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    last_execution_job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ChangeTicketStatement(Base):
    __tablename__ = "change_ticket_statement"
    __table_args__ = (
        UniqueConstraint("ticket_id", "statement_order", name="uq_change_ticket_statement_order"),
    )

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    ticket_id: Mapped[str] = mapped_column(ForeignKey("change_ticket.id", ondelete="CASCADE"), nullable=False)
    statement_order: Mapped[int] = mapped_column(Integer, nullable=False)
    statement_text: Mapped[str] = mapped_column(Text, nullable=False)
    statement_type: Mapped[str] = mapped_column(String(16), nullable=False)
    risk_tags: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    risk_level: Mapped[str] = mapped_column(String(32), nullable=False, default="medium", server_default="medium")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ApprovalInstance(Base):
    __tablename__ = "approval_instance"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    ticket_id: Mapped[str] = mapped_column(ForeignKey("change_ticket.id", ondelete="CASCADE"), nullable=False)
    flow_id: Mapped[str] = mapped_column(ForeignKey("approval_flow.id", ondelete="RESTRICT"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", server_default="pending")
    current_step_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ApprovalInstanceStep(Base):
    __tablename__ = "approval_instance_step"
    __table_args__ = (
        UniqueConstraint("instance_id", "step_no", name="uq_approval_instance_step_order"),
    )

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    instance_id: Mapped[str] = mapped_column(ForeignKey("approval_instance.id", ondelete="CASCADE"), nullable=False)
    flow_step_id: Mapped[str] = mapped_column(ForeignKey("approval_flow_step.id", ondelete="RESTRICT"), nullable=False)
    step_no: Mapped[int] = mapped_column(Integer, nullable=False)
    step_name: Mapped[str] = mapped_column(String(120), nullable=False)
    approval_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    approver_type: Mapped[str] = mapped_column(String(16), nullable=False)
    approver_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    rule: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="waiting", server_default="waiting")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ApprovalAction(Base):
    __tablename__ = "approval_action"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    instance_step_id: Mapped[str] = mapped_column(
        ForeignKey("approval_instance_step.id", ondelete="CASCADE"), nullable=False
    )
    actor_user_id: Mapped[str] = mapped_column(ForeignKey("user_identity.id", ondelete="RESTRICT"), nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ExecutionJob(Base):
    __tablename__ = "execution_job"
    __table_args__ = (
        UniqueConstraint("ticket_id", "run_key", name="uq_execution_job_ticket_run_key"),
    )

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    ticket_id: Mapped[str] = mapped_column(ForeignKey("change_ticket.id", ondelete="CASCADE"), nullable=False)
    run_key: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued", server_default="queued")
    executor_type: Mapped[str] = mapped_column(String(16), nullable=False, default="system", server_default="system")
    executor_user_id: Mapped[str | None] = mapped_column(ForeignKey("user_identity.id", ondelete="SET NULL"), nullable=True)
    execution_mode: Mapped[str] = mapped_column(
        String(16), nullable=False, default="immediate", server_default="immediate"
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_summary: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ExecutionLock(Base):
    __tablename__ = "execution_lock"
    __table_args__ = (
        UniqueConstraint("resource_type", "resource_key", name="uq_execution_lock_resource"),
    )

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    resource_type: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_key: Mapped[str] = mapped_column(String(255), nullable=False)
    owner_job_id: Mapped[str] = mapped_column(ForeignKey("execution_job.id", ondelete="CASCADE"), nullable=False)
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ExecutionStatementResult(Base):
    __tablename__ = "execution_statement_result"
    __table_args__ = (
        UniqueConstraint("job_id", "statement_order", name="uq_execution_statement_result_order"),
    )

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    job_id: Mapped[str] = mapped_column(ForeignKey("execution_job.id", ondelete="CASCADE"), nullable=False)
    statement_order: Mapped[int] = mapped_column(Integer, nullable=False)
    statement_text: Mapped[str] = mapped_column(Text, nullable=False)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    affected_rows: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    db_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    db_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
