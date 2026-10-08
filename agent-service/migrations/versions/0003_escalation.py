"""escalation readiness on case_actions

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Plans made before this migration had no escalation steps: every action was ready.
    op.add_column("case_actions", sa.Column("ready", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("case_actions", sa.Column("waiting_for", sa.String(300), nullable=True))


def downgrade() -> None:
    op.drop_column("case_actions", "waiting_for")
    op.drop_column("case_actions", "ready")
