"""add runtime state tables

Revision ID: 0006_add_runtime_state_tables
Revises: 0005_add_audit_tables
Create Date: 2026-05-25 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0006_add_runtime_state_tables"
down_revision: str | None = "0005_add_audit_tables"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "connection_profile",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["owner_user_id"], ["user_identity.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_connection_profile")),
        sa.UniqueConstraint("owner_user_id", "name", name="uq_connection_profile_owner_name"),
    )
    op.create_table(
        "user_preference",
        sa.Column("id", sa.String(length=96), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("preference_key", sa.String(length=96), nullable=False),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["owner_user_id"], ["user_identity.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_preference")),
        sa.UniqueConstraint("owner_user_id", "preference_key", name="uq_user_preference_owner_key"),
    )
    op.create_table(
        "sidebar_layout_state",
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("layout", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["owner_user_id"], ["user_identity.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("owner_user_id", name=op.f("pk_sidebar_layout_state")),
    )
    op.create_table(
        "saved_sql_folder_state",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("connection_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_user_id"], ["user_identity.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_saved_sql_folder_state")),
    )
    op.create_table(
        "saved_sql_file_state",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("connection_id", sa.String(length=64), nullable=False),
        sa.Column("folder_id", sa.String(length=64), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("database_name", sa.String(length=255), nullable=False),
        sa.Column("schema_name", sa.String(length=255), nullable=True),
        sa.Column("sql_text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["folder_id"], ["saved_sql_folder_state.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["user_identity.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_saved_sql_file_state")),
    )
    op.create_table(
        "query_history_entry",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("connection_id", sa.String(length=64), nullable=True),
        sa.Column("connection_name", sa.String(length=255), nullable=False),
        sa.Column("database_name", sa.String(length=255), nullable=False),
        sa.Column("sql_text", sa.Text(), nullable=False),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("execution_time_ms", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("success", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("activity_kind", sa.String(length=64), nullable=True),
        sa.Column("operation", sa.String(length=128), nullable=True),
        sa.Column("target", sa.String(length=255), nullable=True),
        sa.Column("affected_rows", sa.BigInteger(), nullable=True),
        sa.Column("rollback_sql", sa.Text(), nullable=True),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(["owner_user_id"], ["user_identity.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_query_history_entry")),
    )
    op.create_table(
        "ai_conversation_state",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("connection_name", sa.String(length=255), nullable=False),
        sa.Column("database_name", sa.String(length=255), nullable=False),
        sa.Column("messages", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_user_id"], ["user_identity.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_conversation_state")),
    )


def downgrade() -> None:
    op.drop_table("ai_conversation_state")
    op.drop_table("query_history_entry")
    op.drop_table("saved_sql_file_state")
    op.drop_table("saved_sql_folder_state")
    op.drop_table("sidebar_layout_state")
    op.drop_table("user_preference")
    op.drop_table("connection_profile")
