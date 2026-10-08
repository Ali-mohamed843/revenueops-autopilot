"""executions and outbox_messages

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "executions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("case_id", sa.Uuid(), sa.ForeignKey("cases.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "case_action_id",
            sa.Uuid(),
            sa.ForeignKey("case_actions.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("action_type", sa.String(50), nullable=False),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column("tier", sa.String(20), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("reversible", sa.Boolean(), nullable=False),
        sa.Column("policy_refs", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_by", sa.String(100), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("before", sa.JSON(), nullable=True),
        sa.Column("after", sa.JSON(), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("rolled_back_by", sa.String(100), nullable=True),
        sa.Column("rolled_back_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rollback_result", sa.JSON(), nullable=True),
    )
    op.create_index("ix_executions_case_id", "executions", ["case_id"])
    op.create_table(
        "outbox_messages",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("execution_id", sa.Uuid(), sa.ForeignKey("executions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("case_id", sa.Uuid(), sa.ForeignKey("cases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("recipient", sa.String(200), nullable=False),
        sa.Column("language", sa.String(5), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("drafted_by", sa.String(100), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_outbox_messages_execution_id", "outbox_messages", ["execution_id"])
    op.create_index("ix_outbox_messages_case_id", "outbox_messages", ["case_id"])


def downgrade() -> None:
    op.drop_table("outbox_messages")
    op.drop_table("executions")
