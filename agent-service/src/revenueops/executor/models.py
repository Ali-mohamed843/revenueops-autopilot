from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from revenueops.cases.models import Case, CaseAction, utcnow
from revenueops.db import Base


class ExecutionStatus(StrEnum):
    PENDING_APPROVAL = "pending_approval"  # tier approval: waits for a person to approve it
    AWAITING_HUMAN = "awaiting_human"  # tier human_only: a person does it and reports back
    REJECTED = "rejected"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


OPEN_EXECUTION = (ExecutionStatus.PENDING_APPROVAL, ExecutionStatus.AWAITING_HUMAN)


class Execution(Base):
    """One action carried out (or waiting to be) for a case: the audit record."""

    __tablename__ = "executions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    # One execution per proposed action, ever: nothing runs twice by accident.
    case_action_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("case_actions.id", ondelete="CASCADE"), unique=True)
    action_type: Mapped[str] = mapped_column(String(50))
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    tier: Mapped[str] = mapped_column(String(20))  # as scored when the execution was requested
    status: Mapped[str] = mapped_column(String(30))
    reversible: Mapped[bool] = mapped_column(Boolean)
    policy_refs: Mapped[list[str]] = mapped_column(JSON, default=list)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)  # the investigation's
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    decided_by: Mapped[str | None] = mapped_column(String(100), nullable=True)  # approver, or "auto"
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    before: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    rolled_back_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    rolled_back_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rollback_result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    case: Mapped[Case] = relationship()
    action: Mapped[CaseAction] = relationship()
    messages: Mapped[list[OutboxMessage]] = relationship(
        back_populates="execution", order_by="OutboxMessage.created_at"
    )


class OutboxStatus(StrEnum):
    QUEUED = "queued"  # v1 stops here: nothing is actually sent
    CANCELLED = "cancelled"


class OutboxMessage(Base):
    __tablename__ = "outbox_messages"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    execution_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("executions.id", ondelete="CASCADE"), index=True)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    channel: Mapped[str] = mapped_column(String(20))  # whatsapp | sms | email | vendor | courier
    recipient: Mapped[str] = mapped_column(String(200))
    language: Mapped[str] = mapped_column(String(5))
    body: Mapped[str] = mapped_column(Text)
    drafted_by: Mapped[str] = mapped_column(String(100))  # the model, or "template"
    status: Mapped[str] = mapped_column(String(20), default=OutboxStatus.QUEUED)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    execution: Mapped[Execution] = relationship(back_populates="messages")
