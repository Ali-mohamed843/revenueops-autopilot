from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, Numeric, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from revenueops.cases.models import utcnow
from revenueops.db import Base


class RunKind(StrEnum):
    DRY_RUN = "dry_run"  # what the pipeline would do now; nothing executed
    OUTCOMES = "outcomes"  # a batch of simulated outcomes
    CALIBRATION = "calibration"  # success rates measured from an outcomes run, used by the scorer


class SimulationRun(Base):
    __tablename__ = "simulation_runs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(20), index=True)
    seed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    report: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class Outcome(Base):
    """One simulated episode: a case, the action tried (or none), and whether the revenue came back."""

    __tablename__ = "outcomes"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("simulation_runs.id", ondelete="CASCADE"), index=True)
    case_type: Mapped[str] = mapped_column(String(40))
    action: Mapped[str] = mapped_column(String(50))  # a catalogue key, or "none" for the control group
    segment: Mapped[str] = mapped_column(String(60))
    value: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    recovered: Mapped[bool] = mapped_column(Boolean)
    simulated: Mapped[bool] = mapped_column(Boolean, default=True)  # real outcomes may join later
