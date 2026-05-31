"""add connection secret store

Revision ID: 0008_connection_secret_store
Revises: 0007_config_migration
Create Date: 2026-05-31 00:00:00.000000
"""

from collections.abc import Sequence
import json
import os

from alembic import op
import sqlalchemy as sa
from cryptography.fernet import Fernet


revision: str = "0008_connection_secret_store"
down_revision: str | None = "0007_config_migration"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

SENSITIVE_FIELDS = ("password", "ssh_password", "ssh_key_passphrase", "proxy_password", "connection_string")
CONFIG_SECRET_FIELDS = SENSITIVE_FIELDS + ("secret_fields", "_secret_fields", "secretRefs", "secret_refs")


def upgrade() -> None:
    op.create_table(
        "connection_secret",
        sa.Column("connection_id", sa.String(length=64), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("encrypted_value", sa.Text(), nullable=False),
        sa.Column("encryption_scheme", sa.String(length=32), server_default="fernet", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["connection_id"], ["connection_profile.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("connection_id", "key", name=op.f("pk_connection_secret")),
    )
    op.create_index("ix_connection_secret_connection_id", "connection_secret", ["connection_id"], unique=False)
    _migrate_existing_config_secrets()


def downgrade() -> None:
    op.drop_index("ix_connection_secret_connection_id", table_name="connection_secret")
    op.drop_table("connection_secret")


def _migrate_existing_config_secrets() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            """
            SELECT id, config
            FROM connection_profile
            WHERE config ?| array[
                'password',
                'ssh_password',
                'ssh_key_passphrase',
                'proxy_password',
                'connection_string',
                'secret_fields',
                '_secret_fields',
                'secretRefs',
                'secret_refs'
            ]
            """
        )
    ).mappings().all()
    if not rows:
        return

    fernet: Fernet | None = None
    for row in rows:
        config = _as_mapping(row["config"])
        connection_id = str(row["id"])
        for field in SENSITIVE_FIELDS:
            raw_value = config.get(field)
            if raw_value in (None, ""):
                continue
            if fernet is None:
                fernet = _fernet()
            encrypted_value = fernet.encrypt(str(raw_value).encode("utf-8")).decode("ascii")
            bind.execute(
                sa.text(
                    """
                    INSERT INTO connection_secret (connection_id, key, encrypted_value, encryption_scheme)
                    VALUES (:connection_id, :key, :encrypted_value, 'fernet')
                    ON CONFLICT (connection_id, key)
                    DO UPDATE SET encrypted_value = EXCLUDED.encrypted_value, updated_at = now()
                    """
                ),
                {
                    "connection_id": connection_id,
                    "key": field,
                    "encrypted_value": encrypted_value,
                },
            )
        safe_config = {key: value for key, value in config.items() if key not in CONFIG_SECRET_FIELDS}
        bind.execute(
            sa.text("UPDATE connection_profile SET config = CAST(:config AS jsonb) WHERE id = :connection_id"),
            {"connection_id": connection_id, "config": json.dumps(safe_config)},
        )


def _fernet() -> Fernet:
    key = os.environ.get("DBX_CONNECTION_SECRET_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "DBX_CONNECTION_SECRET_KEY must be configured before migrating existing connection secrets"
        )
    return Fernet(key.encode("ascii"))


def _as_mapping(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    return {}
