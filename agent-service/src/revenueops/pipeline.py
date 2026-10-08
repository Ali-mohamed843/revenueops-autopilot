"""The workflow: detect cases, then investigate them. Works with any StoreAdapter and LLMClient."""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from revenueops.adapters.base import StoreAdapter
from revenueops.agents.investigator import investigate
from revenueops.agents.llm import LLMClient
from revenueops.agents.loop import AgentFailed
from revenueops.cases.models import Case, CaseEvent, CaseStatus
from revenueops.cases.sync import SyncReport, sync_cases
from revenueops.detectors import DetectionResult, Thresholds, run_detectors


@dataclass
class DetectRun:
    result: DetectionResult
    sync: SyncReport


def detect(session: Session, store: StoreAdapter, thresholds: Thresholds | None = None) -> DetectRun:
    now = datetime.now(UTC)
    result = run_detectors(store, now, thresholds)
    return DetectRun(result=result, sync=sync_cases(session, store.name, result, now))


@dataclass
class InvestigateRun:
    investigated: list[uuid.UUID] = field(default_factory=list)
    failed: dict[uuid.UUID, str] = field(default_factory=dict)


def cases_to_investigate(session: Session, store: str, limit: int) -> list[Case]:
    """Open cases nobody has investigated (or whose investigation failed), most urgent first."""
    return list(
        session.scalars(
            select(Case)
            .where(
                Case.store == store,
                Case.status.in_([CaseStatus.OPEN, CaseStatus.INVESTIGATION_FAILED]),
            )
            .order_by(Case.priority, Case.value_at_risk.desc(), Case.detected_at)
            .limit(limit)
        )
    )


def investigate_cases(session: Session, store: StoreAdapter, llm: LLMClient, cases: list[Case]) -> InvestigateRun:
    run = InvestigateRun()
    for case in cases:
        try:
            result = investigate(case, store, llm)
        except AgentFailed as e:
            case.status = CaseStatus.INVESTIGATION_FAILED
            case.events.append(
                CaseEvent(type="investigation_failed", actor="investigator", data={"error": str(e), "model": llm.model})
            )
            run.failed[case.id] = str(e)
        else:
            meta = {
                "model": result.model,
                "turns": result.turns,
                "duration_ms": result.duration_ms,
                "usage": asdict(result.usage),
                "tool_calls": [asdict(c) for c in result.tool_calls],
            }
            case.investigation = {"report": result.output.model_dump(mode="json"), **meta}
            case.status = CaseStatus.INVESTIGATED
            case.events.append(CaseEvent(type="investigated", actor="investigator", data=meta))
            run.investigated.append(case.id)
        session.commit()  # one case at a time: a later failure never loses earlier work
    return run
