"""Turns a detection run into case rows, idempotently."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from revenueops.cases.models import OPEN_STATUSES, Case, CaseEvent, CaseStatus
from revenueops.detectors import DetectionResult

ACTOR = "detector"
CENTS = Decimal("0.01")  # the column's scale; rounding here keeps fresh and reloaded cases identical


@dataclass
class SyncReport:
    opened: int = 0
    updated: int = 0
    reclassified: int = 0
    closed: int = 0


def sync_cases(session: Session, store: str, result: DetectionResult, now: datetime) -> SyncReport:
    """Open, refresh or close cases so they match what the detectors see right now.

    - A subject with no open case gets a new one.
    - A subject with an open case gets its value and signals refreshed (and its type changed if a more
      urgent rule now matches it).
    - An open case whose subject no longer matches is closed as "condition_cleared", but only if its
      detector ran this time: a detector skipped for missing capabilities proves nothing.
    """
    report = SyncReport()
    open_cases = {
        (c.subject_type, c.subject_id): c
        for c in session.scalars(select(Case).where(Case.store == store, Case.status.in_(OPEN_STATUSES)))
    }
    seen: set[tuple[str, str]] = set()

    for cand in result.candidates:
        key = (str(cand.subject_type), cand.subject_id)
        seen.add(key)
        case = open_cases.get(key)
        if case is None:
            case = Case(
                store=store,
                case_type=cand.case_type,
                subject_type=cand.subject_type,
                subject_id=cand.subject_id,
                status=CaseStatus.OPEN,
                title=cand.title,
                value_at_risk=cand.value_at_risk.quantize(CENTS),
                currency=cand.currency,
                priority=cand.priority,
                signals=cand.signals,
                detected_at=now,
                last_seen_at=now,
            )
            case.events.append(
                CaseEvent(type="detected", actor=ACTOR, at=now, data={"case_type": cand.case_type, "title": cand.title})
            )
            session.add(case)
            report.opened += 1
            continue

        if case.case_type != cand.case_type:
            case.events.append(
                CaseEvent(
                    type="reclassified",
                    actor=ACTOR,
                    at=now,
                    data={"from": case.case_type, "to": cand.case_type, "title": cand.title},
                )
            )
            case.case_type = cand.case_type
            report.reclassified += 1
        case.title = cand.title
        case.value_at_risk = cand.value_at_risk.quantize(CENTS)
        case.priority = cand.priority
        case.signals = cand.signals
        case.last_seen_at = now
        report.updated += 1

    ran = {str(t) for t in result.ran}
    for key, case in open_cases.items():
        if key in seen or case.case_type not in ran:
            continue
        case.status = CaseStatus.CLOSED
        case.closed_at = now
        case.close_reason = "condition_cleared"
        case.events.append(CaseEvent(type="closed", actor=ACTOR, at=now, data={"reason": "condition_cleared"}))
        report.closed += 1

    session.commit()
    return report
