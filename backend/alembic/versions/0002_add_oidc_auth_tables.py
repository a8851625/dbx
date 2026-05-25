"""add oidc auth tables

Revision ID: 0002_add_oidc_auth_tables
Revises: 0001_backend_baseline
Create Date: 2026-05-25 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0002_add_oidc_auth_tables"
down_revision: str | None = "0001_backend_baseline"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "identity_provider",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("issuer", sa.String(length=255), nullable=True),
        sa.Column("authorize_url", sa.String(length=500), nullable=False),
        sa.Column("token_url", sa.String(length=500), nullable=False),
        sa.Column("userinfo_url", sa.String(length=500), nullable=False),
        sa.Column("logout_url", sa.String(length=500), nullable=True),
        sa.Column("client_id", sa.String(length=255), nullable=False),
        sa.Column("scopes", sa.String(length=255), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("id", name="pk_identity_provider"),
    )
    op.create_table(
        "user_identity",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("provider_id", sa.String(length=64), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=True),
        sa.Column("username", sa.String(length=255), nullable=True),
        sa.Column("role", sa.String(length=64), nullable=False, server_default="admin"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("claims", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["provider_id"], ["identity_provider.id"], name="fk_user_identity_provider_id_identity_provider", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_user_identity"),
        sa.UniqueConstraint("email", name="uq_user_identity_email"),
        sa.UniqueConstraint("provider_id", "subject", name="uq_user_identity_provider_subject"),
    )
    op.create_table(
        "oidc_auth_request",
        sa.Column("state", sa.String(length=255), nullable=False),
        sa.Column("provider_id", sa.String(length=64), nullable=False),
        sa.Column("nonce", sa.String(length=255), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["provider_id"], ["identity_provider.id"], name="fk_oidc_auth_request_provider_id_identity_provider", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("state", name="pk_oidc_auth_request"),
    )
    op.create_table(
        "user_session",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("provider_id", sa.String(length=64), nullable=False),
        sa.Column("session_token", sa.String(length=255), nullable=False),
        sa.Column("state", sa.String(length=255), nullable=True),
        sa.Column("nonce", sa.String(length=255), nullable=True),
        sa.Column("source_ip", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["provider_id"], ["identity_provider.id"], name="fk_user_session_provider_id_identity_provider", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["user_identity.id"], name="fk_user_session_user_id_user_identity", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_user_session"),
        sa.UniqueConstraint("session_token", name="uq_user_session_session_token"),
    )


def downgrade() -> None:
    op.drop_table("user_session")
    op.drop_table("oidc_auth_request")
    op.drop_table("user_identity")
    op.drop_table("identity_provider")
