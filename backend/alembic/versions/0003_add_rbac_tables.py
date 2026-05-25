"""add rbac baseline tables

Revision ID: 0003_add_rbac_tables
Revises: 0002_add_oidc_auth_tables
Create Date: 2026-05-25 10:30:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0003_add_rbac_tables"
down_revision: str | None = "0002_add_oidc_auth_tables"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "role",
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("built_in", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("code", name="pk_role"),
    )
    op.create_table(
        "permission",
        sa.Column("code", sa.String(length=96), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("code", name="pk_permission"),
    )
    op.create_table(
        "role_permission",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("role_code", sa.String(length=64), nullable=False),
        sa.Column("permission_code", sa.String(length=96), nullable=False),
        sa.Column("effect", sa.String(length=16), nullable=False, server_default="allow"),
        sa.Column("conditions", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["permission_code"], ["permission.code"], name="fk_role_permission_permission_code_permission", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["role_code"], ["role.code"], name="fk_role_permission_role_code_role", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_role_permission"),
        sa.UniqueConstraint("role_code", "permission_code", name="uq_role_permission_binding"),
    )
    op.create_table(
        "user_role_binding",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("role_code", sa.String(length=64), nullable=False),
        sa.Column("granted_by", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["granted_by"], ["user_identity.id"], name="fk_user_role_binding_granted_by_user_identity", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["role_code"], ["role.code"], name="fk_user_role_binding_role_code_role", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["user_identity.id"], name="fk_user_role_binding_user_id_user_identity", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_user_role_binding"),
        sa.UniqueConstraint("user_id", "role_code", name="uq_user_role_binding_user_role"),
    )
    op.create_table(
        "resource_policy",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("principal_type", sa.String(length=16), nullable=False),
        sa.Column("principal_ref", sa.String(length=128), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=False),
        sa.Column("resource_key", sa.String(length=255), nullable=False),
        sa.Column("permission_code", sa.String(length=96), nullable=True),
        sa.Column("effect", sa.String(length=16), nullable=False, server_default="allow"),
        sa.Column("conditions", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("id", name="pk_resource_policy"),
    )


def downgrade() -> None:
    op.drop_table("resource_policy")
    op.drop_table("user_role_binding")
    op.drop_table("role_permission")
    op.drop_table("permission")
    op.drop_table("role")
