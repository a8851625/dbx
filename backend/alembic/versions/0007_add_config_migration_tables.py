"""add config migration tables

Revision ID: 0007_add_config_migration_tables
Revises: 0006_add_runtime_state_tables
Create Date: 2026-05-26 12:00:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0007_add_config_migration_tables"
down_revision: str | None = "0006_add_runtime_state_tables"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "system_setting",
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("updated_by_user_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["user_identity.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_system_setting")),
    )
    op.create_table(
        "legacy_import_job",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("job_type", sa.String(length=64), server_default="legacy_state_import", nullable=False),
        sa.Column("source_kind", sa.String(length=16), nullable=False),
        sa.Column("source_label", sa.String(length=512), nullable=False),
        sa.Column("source_checksum", sa.String(length=64), nullable=False),
        sa.Column("target_user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("status", sa.String(length=24), server_default="pending", nullable=False),
        sa.Column("overwrite_existing", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("options", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["user_identity.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["target_user_id"], ["user_identity.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_legacy_import_job")),
    )
    op.create_index("ix_legacy_import_job_target_user_id", "legacy_import_job", ["target_user_id"], unique=False)
    op.create_index("ix_legacy_import_job_status", "legacy_import_job", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_legacy_import_job_status", table_name="legacy_import_job")
    op.drop_index("ix_legacy_import_job_target_user_id", table_name="legacy_import_job")
    op.drop_table("legacy_import_job")
    op.drop_table("system_setting")
