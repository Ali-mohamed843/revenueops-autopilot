"""A dry run: what the pipeline would do right now, without doing any of it.

Detection reads the store; every case found is then scored exactly as the executor would score it,
and a recorder takes the executor's place. Nothing is written to the store and nothing is sent; the
only write is the report itself. Cases that already have a plan use it; the rest use every catalogue
action that fits, with default parameters (no model is called, so a dry run is fast and free).
"""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from revenueops.adapters.base import StoreAdapter
from revenueops.cases.models import OPEN_STATUSES, ActionStatus, Case
from revenueops.decision.catalogue import CATALOGUE, Kind, actions_for
from revenueops.decision.rates import RateTable
from revenueops.decision.score import CaseFacts, Score, Tier, rank, recommended, score
from revenueops.detectors import run_detectors
from revenueops.executor.engine import capabilities_of, executions_of
from revenueops.executor.models import ExecutionStatus
from revenueops.simulation.models import RunKind, SimulationRun

DEFAULT_PARAMS: dict[str, dict[str, Any]] = {
    "send_confirmation_reminder": {"channel": "whatsapp"},
    "send_cart_reminder": {"channel": "whatsapp"},
    "notify_customer_of_delay": {"channel": "whatsapp", "new_eta_days": 3},
    "request_deposit": {"percent": 10},
    "offer_discount": {"percent": 10, "valid_hours": 48},
    "process_return": {"decision": "approve"},
}

HIGH_RISK_LIMIT = 15  # the report lists at most this many high-risk actions


def is_high_risk(s: Score) -> bool:
    """Needs a person, moves money, or can't be undone."""
    spec = CATALOGUE[s.action]
    return s.tier != Tier.AUTO and (spec.kind == Kind.MONEY or not spec.reversible or s.tier == Tier.HUMAN_ONLY)


def dry_run(
    session: Session,
    store: StoreAdapter,
    rates: RateTable | None,
    rates_source: str | None = None,
    now: datetime | None = None,
) -> SimulationRun:
    now = now or datetime.now(UTC)
    has = capabilities_of(store)
    candidates = run_detectors(store, now).candidates
    open_cases = {
        (c.subject_type, c.subject_id): c for c in session.scalars(select(Case).where(Case.status.in_(OPEN_STATUSES)))
    }

    tiers: Counter[str] = Counter()
    tier_value: dict[str, Decimal] = {t: Decimal(0) for t in ("auto", "approval", "human_only", "waiting")}
    actions: Counter[str] = Counter()
    expected = Decimal(0)
    planned_by: Counter[str] = Counter()
    high_risk: list[dict[str, Any]] = []
    measured = 0

    for cand in candidates:
        case = open_cases.get((str(cand.subject_type), cand.subject_id))
        available = frozenset(a.key for a in actions_for(cand.case_type, has))
        executions = executions_of(session, case) if case else []
        if any(e.status in (ExecutionStatus.PENDING_APPROVAL, ExecutionStatus.AWAITING_HUMAN) for e in executions):
            tiers["waiting"] += 1
            tier_value["waiting"] += cand.value_at_risk
            continue
        done = {
            e.action_type: (now - _aware(e.finished_at)).total_seconds() / 3600
            for e in executions
            if e.status == ExecutionStatus.SUCCEEDED and e.finished_at is not None
        }
        tried = {e.action_type for e in executions}
        report = (case.investigation or {}).get("report", {}) if case else {}
        facts = CaseFacts(
            case_type=str(cand.case_type),
            value_at_risk=cand.value_at_risk,
            confidence=report.get("confidence"),
            done_hours_ago=done,
            available=available,
            rates=rates,
        )

        if case and case.plan and any(a.status == ActionStatus.PROPOSED for a in case.actions):
            proposals = [(a.action_type, a.params) for a in case.actions if a.status == ActionStatus.PROPOSED]
            planned_by["plan"] += 1
        else:
            proposals = [(a.key, DEFAULT_PARAMS.get(a.key, {})) for a in actions_for(cand.case_type, has)]
            planned_by["catalogue"] += 1

        scored = []
        for action, params in proposals:
            if action in tried or action not in available:
                continue
            try:
                checked = CATALOGUE[action].check_params(params, cand.value_at_risk)
            except ValueError:
                continue
            scored.append(score(action, checked, facts))
        best = recommended(rank(scored))
        if best is None:
            tiers["nothing_ready"] += 1
            continue

        tiers[str(best.tier)] += 1
        tier_value[str(best.tier)] += cand.value_at_risk
        actions[best.action] += 1
        expected += best.expected_value
        measured += int(best.measured)
        if is_high_risk(best):
            high_risk.append(
                {
                    "case_id": str(case.id) if case else None,
                    "case_type": str(cand.case_type),
                    "subject": f"{cand.subject_type} {cand.subject_id[:8]}",
                    "value": str(cand.value_at_risk),
                    "action": best.action,
                    "tier": str(best.tier),
                    "reason": next((r.text for r in best.reasons if r.tier == best.tier), ""),
                    "rule": next((r.rule for r in best.reasons if r.tier == best.tier), None),
                }
            )

    acting = sum(tiers[t] for t in ("auto", "approval", "human_only"))
    high_risk.sort(key=lambda h: -Decimal(h["value"]))
    run = SimulationRun(
        id=uuid.uuid4(),
        kind=RunKind.DRY_RUN,
        params={"rates": rates_source or "starting estimates"},
        report={
            "cases": len(candidates),
            "value_at_risk": str(sum((c.value_at_risk for c in candidates), Decimal(0))),
            "tiers": {k: tiers[k] for k in ("auto", "approval", "human_only", "waiting", "nothing_ready")},
            "tier_value": {k: str(v) for k, v in tier_value.items()},
            "actions": dict(actions.most_common()),
            "expected_recovery": str(expected.quantize(Decimal("0.01"))),
            "approvals_needed": tiers["approval"] + tiers["human_only"],
            "measured_share": round(measured / acting, 4) if acting else None,
            "planned_by": dict(planned_by),
            "high_risk": high_risk[:HIGH_RISK_LIMIT],
            "high_risk_total": len(high_risk),
        },
    )
    session.add(run)
    session.commit()
    return run


def _aware(at: datetime) -> datetime:
    return at if at.tzinfo else at.replace(tzinfo=UTC)
