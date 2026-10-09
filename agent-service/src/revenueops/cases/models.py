from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, Numeric, String, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from revenueops.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class CaseType(StrEnum):
    REFUSAL_RISK = "refusal_risk"  # risky COD order not yet shipped
    UNCONFIRMED_ORDER = "unconfirmed_order"  # pending too long without confirmation
    STALLED_FULFILMENT = "stalled_fulfilment"  # confirmed but not dispatched
    LATE_SHIPMENT = "late_shipment"  # on the road past the courier's promise
    ABANDONED_CART = "abandoned_cart"
    STALE_RETURN = "stale_return"  # return request nobody acted on


class SubjectType(StrEnum):
    ORDER = "order"
    CART = "cart"
    RETURN = "return"


class CaseStatus(StrEnum):
    OPEN = "open"  # detected, not investigated yet
    INVESTIGATED = "investigated"
    INVESTIGATION_FAILED = "investigation_failed"  # retryable
    PLANNED = "planned"  # actions proposed and scored
    PLANNING_FAILED = "planning_failed"  # retryable
    ACTING = "acting"  # an action waits for approval or for a person to do it
    ACTED = "acted"  # an action ran; waiting to see if it worked, or for the next escalation step
    CLOSED = "closed"


OPEN_STATUSES = (
    CaseStatus.OPEN,
    CaseStatus.INVESTIGATED,
    CaseStatus.INVESTIGATION_FAILED,
    CaseStatus.PLANNED,
    CaseStatus.PLANNING_FAILED,
    CaseStatus.ACTING,
    CaseStatus.ACTED,
)


class Case(Base):
    __tablename__ = "cases"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    store: Mapped[str] = mapped_column(String(50))
    case_type: Mapped[str] = mapped_column(String(40))
    subject_type: Mapped[str] = mapped_column(String(20))
    subject_id: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(30), default=CaseStatus.OPEN)
    title: Mapped[str] = mapped_column(String(300))
    value_at_risk: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3))
    priority: Mapped[int]
    signals: Mapped[dict[str, Any]] = mapped_column(JSON)  # what the detector saw
    investigation: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    plan: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)  # the Strategist's run
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    close_reason: Mapped[str | None] = mapped_column(String(100), nullable=True)

    events: Mapped[list[CaseEvent]] = relationship(
        back_populates="case", order_by="CaseEvent.at", cascade="all, delete-orphan"
    )
    actions: Mapped[list[CaseAction]] = relationship(
        back_populates="case", order_by="CaseAction.rank", cascade="all, delete-orphan"
    )

    __table_args__ = (
        # At most one open case per subject, enforced by the database.
        Index(
            "uq_cases_open_subject",
            "store",
            "subject_type",
            "subject_id",
            unique=True,
            postgresql_where=text("status <> 'closed'"),
            sqlite_where=text("status <> 'closed'"),
        ),
        Index("ix_cases_status_priority", "status", "priority"),
    )


class CaseEvent(Base):
    """Append-only audit trail: what happened to a case, when, and who did it."""

    __tablename__ = "case_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(String(40))
    actor: Mapped[str] = mapped_column(String(40))  # "detector", "investigator", ...
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    case: Mapped[Case] = relationship(back_populates="events")


class ActionStatus(StrEnum):
    PROPOSED = "proposed"
    SUPERSEDED = "superseded"  # replaced by a newer plan


class CaseAction(Base):
    """One proposed action for a case, with the score and tier the decision engine gave it."""

    __tablename__ = "case_actions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    rank: Mapped[int] = mapped_column(Integer)  # 1 = best
    recommended: Mapped[bool] = mapped_column(Boolean, default=False)
    action_type: Mapped[str] = mapped_column(String(50))
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    rationale: Mapped[str] = mapped_column(String(1000))
    policy_refs: Mapped[list[str]] = mapped_column(JSON, default=list)
    p_with: Mapped[float] = mapped_column(Float)
    p_without: Mapped[float] = mapped_column(Float)
    cost: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    expected_value: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    tier: Mapped[str] = mapped_column(String(20))  # auto | approval | human_only
    tier_reasons: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    ready: Mapped[bool] = mapped_column(Boolean, default=True)  # False: a later escalation step
    measured: Mapped[bool] = mapped_column(Boolean, default=False)  # chances measured, not assumed
    trials: Mapped[int] = mapped_column(Integer, default=0)  # trials behind the measured chance
    waiting_for: Mapped[str | None] = mapped_column(String(300), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default=ActionStatus.PROPOSED)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    case: Mapped[Case] = relationship(back_populates="actions")
