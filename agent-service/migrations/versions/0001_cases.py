"""cases and case_events

Revision ID: 0001
Revises:
Create Date: 2026-10-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "cases",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("store", sa.String(50), nullable=False),
        sa.Column("case_type", sa.String(40), nullable=False),
        sa.Column("subject_type", sa.String(20), nullable=False),
        sa.Column("subject_id", sa.String(100), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("value_at_risk", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("signals", sa.JSON(), nullable=False),
        sa.Column("investigation", sa.JSON(), nullable=True),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("close_reason", sa.String(100), nullable=True),
    )
    # At most one open case per subject
    op.create_index(
        "uq_cases_open_subject",
        "cases",
        ["store", "subject_type", "subject_id"],
        unique=True,
        postgresql_where=sa.text("status <> 'closed'"),
        sqlite_where=sa.text("status <> 'closed'"),
    )
    op.create_index("ix_cases_status_priority", "cases", ["status", "priority"])

    op.create_table(
        "case_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("case_id", sa.Uuid(), sa.ForeignKey("cases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("type", sa.String(40), nullable=False),
        sa.Column("actor", sa.String(40), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_case_events_case_id", "case_events", ["case_id"])


def downgrade() -> None:
    op.drop_table("case_events")
    op.drop_table("cases")
