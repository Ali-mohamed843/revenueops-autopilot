"""case plans and case_actions

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("cases", sa.Column("plan", sa.JSON(), nullable=True))
    op.create_table(
        "case_actions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("case_id", sa.Uuid(), sa.ForeignKey("cases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("recommended", sa.Boolean(), nullable=False),
        sa.Column("action_type", sa.String(50), nullable=False),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column("rationale", sa.String(1000), nullable=False),
        sa.Column("policy_refs", sa.JSON(), nullable=False),
        sa.Column("p_with", sa.Float(), nullable=False),
        sa.Column("p_without", sa.Float(), nullable=False),
        sa.Column("cost", sa.Numeric(12, 2), nullable=False),
        sa.Column("expected_value", sa.Numeric(12, 2), nullable=False),
        sa.Column("tier", sa.String(20), nullable=False),
        sa.Column("tier_reasons", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_case_actions_case_id", "case_actions", ["case_id"])


def downgrade() -> None:
    op.drop_table("case_actions")
    op.drop_column("cases", "plan")
