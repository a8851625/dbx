"""add approval workflow and execution tables

Revision ID: 0004_add_approval_workflow_tables
Revises: 0003_add_rbac_tables
Create Date: 2026-05-25 18:20:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0004_add_approval_workflow_tables"
down_revision: str | None = "0003_add_rbac_tables"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "approval_flow",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("ticket_type", sa.String(length=32), nullable=False),
        sa.Column("match_rule", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("built_in", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("id", name="pk_approval_flow"),
        sa.UniqueConstraint("code", name="uq_approval_flow_code"),
    )
    op.create_table(
        "approval_flow_step",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("flow_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("step_no", sa.Integer(), nullable=False),
        sa.Column("step_name", sa.String(length=120), nullable=False),
        sa.Column("approval_mode", sa.String(length=16), nullable=False, server_default="any_one"),
        sa.Column("approver_type", sa.String(length=16), nullable=False),
        sa.Column("approver_ref", sa.String(length=128), nullable=False),
        sa.Column("rule", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["flow_id"], ["approval_flow.id"], name="fk_approval_flow_step_flow_id_approval_flow", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_approval_flow_step"),
        sa.UniqueConstraint("flow_id", "step_no", name="uq_approval_flow_step_order"),
    )
    op.create_table(
        "change_ticket",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("ticket_no", sa.String(length=40), nullable=False),
        sa.Column("ticket_type", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("datasource_id", sa.String(length=128), nullable=False),
        sa.Column("target_database", sa.String(length=128), nullable=False),
        sa.Column("target_schema", sa.String(length=128), nullable=True),
        sa.Column("target_table", sa.String(length=128), nullable=True),
        sa.Column("risk_level", sa.String(length=32), nullable=False, server_default="medium"),
        sa.Column("sql_text", sa.Text(), nullable=False),
        sa.Column("sql_summary", sa.Text(), nullable=True),
        sa.Column("submitter_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("current_status", sa.String(length=32), nullable=False, server_default="draft"),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("latest_flow_instance_id", sa.String(length=36), nullable=True),
        sa.Column("last_execution_job_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["submitter_id"], ["user_identity.id"], name="fk_change_ticket_submitter_id_user_identity", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_change_ticket"),
        sa.UniqueConstraint("ticket_no", name="uq_change_ticket_ticket_no"),
    )
    op.create_index("ix_change_ticket_status_scheduled_at", "change_ticket", ["current_status", "scheduled_at"])
    op.create_table(
        "change_ticket_statement",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("ticket_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("statement_order", sa.Integer(), nullable=False),
        sa.Column("statement_text", sa.Text(), nullable=False),
        sa.Column("statement_type", sa.String(length=16), nullable=False),
        sa.Column("risk_tags", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("risk_level", sa.String(length=32), nullable=False, server_default="medium"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["ticket_id"], ["change_ticket.id"], name="fk_change_ticket_statement_ticket_id_change_ticket", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_change_ticket_statement"),
        sa.UniqueConstraint("ticket_id", "statement_order", name="uq_change_ticket_statement_order"),
    )
    op.create_table(
        "approval_instance",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("ticket_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("flow_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("current_step_no", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["flow_id"], ["approval_flow.id"], name="fk_approval_instance_flow_id_approval_flow", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["ticket_id"], ["change_ticket.id"], name="fk_approval_instance_ticket_id_change_ticket", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_approval_instance"),
    )
    op.create_table(
        "approval_instance_step",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("instance_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("flow_step_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("step_no", sa.Integer(), nullable=False),
        sa.Column("step_name", sa.String(length=120), nullable=False),
        sa.Column("approval_mode", sa.String(length=16), nullable=False),
        sa.Column("approver_type", sa.String(length=16), nullable=False),
        sa.Column("approver_ref", sa.String(length=128), nullable=False),
        sa.Column("rule", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="waiting"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["flow_step_id"], ["approval_flow_step.id"], name="fk_approval_instance_step_flow_step_id_approval_flow_step", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["instance_id"], ["approval_instance.id"], name="fk_approval_instance_step_instance_id_approval_instance", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_approval_instance_step"),
        sa.UniqueConstraint("instance_id", "step_no", name="uq_approval_instance_step_order"),
    )
    op.create_index("ix_approval_instance_step_status", "approval_instance_step", ["status"])
    op.create_table(
        "approval_action",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("instance_step_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["actor_user_id"], ["user_identity.id"], name="fk_approval_action_actor_user_id_user_identity", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["instance_step_id"], ["approval_instance_step.id"], name="fk_approval_action_instance_step_id_approval_instance_step", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_approval_action"),
    )
    op.create_table(
        "execution_job",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("ticket_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("run_key", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="queued"),
        sa.Column("executor_type", sa.String(length=16), nullable=False, server_default="system"),
        sa.Column("executor_user_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("execution_mode", sa.String(length=16), nullable=False, server_default="immediate"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("result_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["executor_user_id"], ["user_identity.id"], name="fk_execution_job_executor_user_id_user_identity", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["ticket_id"], ["change_ticket.id"], name="fk_execution_job_ticket_id_change_ticket", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_execution_job"),
        sa.UniqueConstraint("ticket_id", "run_key", name="uq_execution_job_ticket_run_key"),
    )
    op.create_index("ix_execution_job_ticket_status", "execution_job", ["ticket_id", "status"])
    op.create_table(
        "execution_lock",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("resource_type", sa.String(length=32), nullable=False),
        sa.Column("resource_key", sa.String(length=255), nullable=False),
        sa.Column("owner_job_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_job_id"], ["execution_job.id"], name="fk_execution_lock_owner_job_id_execution_job", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_execution_lock"),
        sa.UniqueConstraint("resource_type", "resource_key", name="uq_execution_lock_resource"),
    )
    op.create_table(
        "execution_statement_result",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("statement_order", sa.Integer(), nullable=False),
        sa.Column("statement_text", sa.Text(), nullable=False),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("affected_rows", sa.BigInteger(), nullable=True),
        sa.Column("duration_ms", sa.BigInteger(), nullable=True),
        sa.Column("db_error_code", sa.String(length=64), nullable=True),
        sa.Column("db_error_message", sa.Text(), nullable=True),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["job_id"], ["execution_job.id"], name="fk_execution_statement_result_job_id_execution_job", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_execution_statement_result"),
        sa.UniqueConstraint("job_id", "statement_order", name="uq_execution_statement_result_order"),
    )


def downgrade() -> None:
    op.drop_table("execution_statement_result")
    op.drop_table("execution_lock")
    op.drop_index("ix_execution_job_ticket_status", table_name="execution_job")
    op.drop_table("execution_job")
    op.drop_table("approval_action")
    op.drop_index("ix_approval_instance_step_status", table_name="approval_instance_step")
    op.drop_table("approval_instance_step")
    op.drop_table("approval_instance")
    op.drop_table("change_ticket_statement")
    op.drop_index("ix_change_ticket_status_scheduled_at", table_name="change_ticket")
    op.drop_table("change_ticket")
    op.drop_table("approval_flow_step")
    op.drop_table("approval_flow")
