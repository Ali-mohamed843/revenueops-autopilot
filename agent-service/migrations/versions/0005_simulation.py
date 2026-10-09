"""simulation runs, outcomes, and measured-rate flags on case_actions

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "simulation_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("seed", sa.Integer(), nullable=True),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column("report", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_simulation_runs_kind", "simulation_runs", ["kind"])
    op.create_index("ix_simulation_runs_created_at", "simulation_runs", ["created_at"])
    op.create_table(
        "outcomes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("simulation_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("case_type", sa.String(40), nullable=False),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("segment", sa.String(60), nullable=False),
        sa.Column("value", sa.Numeric(12, 2), nullable=False),
        sa.Column("recovered", sa.Boolean(), nullable=False),
        sa.Column("simulated", sa.Boolean(), nullable=False),
    )
    op.create_index("ix_outcomes_run_id", "outcomes", ["run_id"])
    # Plans made before calibration used the starting estimates.
    op.add_column("case_actions", sa.Column("measured", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("case_actions", sa.Column("trials", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("case_actions", "trials")
    op.drop_column("case_actions", "measured")
    op.drop_table("outcomes")
    op.drop_table("simulation_runs")
