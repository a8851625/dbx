"""add structured audit tables

Revision ID: 0005_add_audit_tables
Revises: 0004_add_approval_workflow_tables
Create Date: 2026-05-25 19:35:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0005_add_audit_tables"
down_revision: str | None = "0004_add_approval_workflow_tables"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_event",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("event_type", sa.String(length=96), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False, server_default="success"),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("actor_email", sa.String(length=255), nullable=True),
        sa.Column("actor_display_name", sa.String(length=255), nullable=True),
        sa.Column("actor_role", sa.String(length=64), nullable=True),
        sa.Column("source_ip", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("request_path", sa.String(length=255), nullable=True),
        sa.Column("request_method", sa.String(length=16), nullable=True),
        sa.Column("resource_type", sa.String(length=64), nullable=True),
        sa.Column("resource_id", sa.String(length=128), nullable=True),
        sa.Column("resource_name", sa.String(length=255), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["actor_user_id"], ["user_identity.id"], name="fk_audit_event_actor_user_id_user_identity", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_audit_event"),
    )
    op.create_index("ix_audit_event_created_at", "audit_event", ["created_at"])
    op.create_index("ix_audit_event_category_created_at", "audit_event", ["category", "created_at"])
    op.create_index("ix_audit_event_actor_user_id", "audit_event", ["actor_user_id"])
    op.create_index("ix_audit_event_resource_type_resource_id", "audit_event", ["resource_type", "resource_id"])

    op.create_table(
        "query_audit",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("execution_id", sa.String(length=128), nullable=True),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("actor_email", sa.String(length=255), nullable=True),
        sa.Column("actor_display_name", sa.String(length=255), nullable=True),
        sa.Column("actor_role", sa.String(length=64), nullable=True),
        sa.Column("source_ip", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("request_path", sa.String(length=255), nullable=True),
        sa.Column("request_method", sa.String(length=16), nullable=True),
        sa.Column("datasource_id", sa.String(length=128), nullable=False),
        sa.Column("database_name", sa.String(length=128), nullable=False),
        sa.Column("schema_name", sa.String(length=128), nullable=True),
        sa.Column("table_name", sa.String(length=128), nullable=True),
        sa.Column("operation_type", sa.String(length=32), nullable=False, server_default="interactive"),
        sa.Column("execution_mode", sa.String(length=32), nullable=False, server_default="immediate"),
        sa.Column("statement_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("sql_text", sa.Text(), nullable=False),
        sa.Column("sql_summary", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="succeeded"),
        sa.Column("duration_ms", sa.BigInteger(), nullable=True),
        sa.Column("affected_rows", sa.BigInteger(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["actor_user_id"], ["user_identity.id"], name="fk_query_audit_actor_user_id_user_identity", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_query_audit"),
    )
    op.create_index("ix_query_audit_created_at", "query_audit", ["created_at"])
    op.create_index("ix_query_audit_datasource_id_database_name", "query_audit", ["datasource_id", "database_name"])
    op.create_index("ix_query_audit_actor_user_id", "query_audit", ["actor_user_id"])
    op.create_index("ix_query_audit_status_created_at", "query_audit", ["status", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_query_audit_status_created_at", table_name="query_audit")
    op.drop_index("ix_query_audit_actor_user_id", table_name="query_audit")
    op.drop_index("ix_query_audit_datasource_id_database_name", table_name="query_audit")
    op.drop_index("ix_query_audit_created_at", table_name="query_audit")
    op.drop_table("query_audit")
    op.drop_index("ix_audit_event_resource_type_resource_id", table_name="audit_event")
    op.drop_index("ix_audit_event_actor_user_id", table_name="audit_event")
    op.drop_index("ix_audit_event_category_created_at", table_name="audit_event")
    op.drop_index("ix_audit_event_created_at", table_name="audit_event")
    op.drop_table("audit_event")
