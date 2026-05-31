"""add request and trace identifiers to audit tables

Revision ID: 0008_audit_trace_columns
Revises: 0007_config_migration
Create Date: 2026-05-31 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0008_audit_trace_columns"
down_revision: str | None = "0007_config_migration"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("audit_event", sa.Column("request_id", sa.String(length=128), nullable=True))
    op.add_column("audit_event", sa.Column("trace_id", sa.String(length=128), nullable=True))
    op.add_column("query_audit", sa.Column("request_id", sa.String(length=128), nullable=True))
    op.add_column("query_audit", sa.Column("trace_id", sa.String(length=128), nullable=True))
    op.create_index("ix_audit_event_request_id", "audit_event", ["request_id"])
    op.create_index("ix_audit_event_trace_id", "audit_event", ["trace_id"])
    op.create_index("ix_query_audit_request_id", "query_audit", ["request_id"])
    op.create_index("ix_query_audit_trace_id", "query_audit", ["trace_id"])


def downgrade() -> None:
    op.drop_index("ix_query_audit_trace_id", table_name="query_audit")
    op.drop_index("ix_query_audit_request_id", table_name="query_audit")
    op.drop_index("ix_audit_event_trace_id", table_name="audit_event")
    op.drop_index("ix_audit_event_request_id", table_name="audit_event")
    op.drop_column("query_audit", "trace_id")
    op.drop_column("query_audit", "request_id")
    op.drop_column("audit_event", "trace_id")
    op.drop_column("audit_event", "request_id")
