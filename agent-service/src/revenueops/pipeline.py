"""The workflow: detect cases, investigate them, then plan actions. Works with any StoreAdapter and LLMClient."""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field, fields
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from revenueops.adapters.base import StoreAdapter
from revenueops.agents.investigator import investigate
from revenueops.agents.llm import LLMClient, LLMError, RateLimited
from revenueops.agents.loop import AgentFailed, AgentResult
from revenueops.agents.strategist import Strategy, plan
from revenueops.cases.models import ActionStatus, Case, CaseAction, CaseEvent, CaseStatus
from revenueops.cases.sync import SyncReport, sync_cases
from revenueops.commerce.models import Capabilities
from revenueops.decision.catalogue import CATALOGUE, actions_for
from revenueops.decision.score import CaseFacts, rank, recommended, score
from revenueops.detectors import DetectionResult, Thresholds, run_detectors
from revenueops.policies import policies_for


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
    stopped: str | None = None  # why the run ended before reaching every case


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
        except (AgentFailed, LLMError) as e:
            case.status = CaseStatus.INVESTIGATION_FAILED
            case.events.append(
                CaseEvent(type="investigation_failed", actor="investigator", data={"error": str(e), "model": llm.model})
            )
            run.failed[case.id] = str(e)
            if isinstance(e, RateLimited):
                session.commit()
                run.stopped = str(e)  # the next case would hit the same limit
                break
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


# ------------------------------------------------------------------ planning


@dataclass
class PlanRun:
    planned: list[uuid.UUID] = field(default_factory=list)
    failed: dict[uuid.UUID, str] = field(default_factory=dict)
    stopped: str | None = None


def cases_to_plan(session: Session, store: str, limit: int) -> list[Case]:
    """Investigated cases without a plan (or whose planning failed), most urgent first."""
    return list(
        session.scalars(
            select(Case)
            .where(
                Case.store == store,
                Case.status.in_([CaseStatus.INVESTIGATED, CaseStatus.PLANNING_FAILED]),
            )
            .order_by(Case.priority, Case.value_at_risk.desc(), Case.detected_at)
            .limit(limit)
        )
    )


def plan_cases(session: Session, store: StoreAdapter, llm: LLMClient, cases: list[Case]) -> PlanRun:
    run = PlanRun()
    has = {f.name for f in fields(Capabilities) if store.capabilities.has(f.name)}
    for case in cases:
        actions = actions_for(case.case_type, has)
        policies = policies_for(case.case_type)
        try:
            if not actions:
                raise AgentFailed(f"No catalogue action fits {case.case_type} on {store.name}")
            result = plan(case, actions, policies, llm)
        except (AgentFailed, LLMError) as e:
            case.status = CaseStatus.PLANNING_FAILED
            case.events.append(
                CaseEvent(type="planning_failed", actor="strategist", data={"error": str(e), "model": llm.model})
            )
            run.failed[case.id] = str(e)
            if isinstance(e, RateLimited):
                session.commit()
                run.stopped = str(e)
                break
        else:
            save_plan(case, result, {a.key for a in actions})
            run.planned.append(case.id)
        session.commit()
    return run


def save_plan(case: Case, result: AgentResult[Strategy], available: set[str]) -> None:
    """Score the Strategist's proposals and store them ranked. The scores, not the model, set the tiers."""
    report = (case.investigation or {}).get("report", {})
    facts = CaseFacts(
        case.case_type,
        case.value_at_risk,
        report.get("confidence"),
        done_hours_ago={},  # nothing is executed before Phase 4, so every escalation starts at step one
        available=frozenset(available),
    )
    pairs = []
    for proposal in result.output.proposals:
        params = CATALOGUE[proposal.action].check_params(proposal.params, case.value_at_risk)
        pairs.append((score(proposal.action, params, facts), proposal))
    ranked = rank([s for s, _ in pairs])
    best = recommended(ranked)
    proposal_of = {id(s): p for s, p in pairs}

    for old in case.actions:
        if old.status == ActionStatus.PROPOSED:
            old.status = ActionStatus.SUPERSEDED
    for i, s in enumerate(ranked, start=1):
        p = proposal_of[id(s)]
        case.actions.append(
            CaseAction(
                rank=i,
                recommended=s is best,
                action_type=s.action,
                params=_json_safe(s.params),
                rationale=p.rationale,
                policy_refs=p.policy_refs,
                p_with=s.p_with,
                p_without=s.p_without,
                cost=s.cost,
                expected_value=s.expected_value,
                tier=str(s.tier),
                tier_reasons=[{"tier": str(r.tier), "text": r.text, "rule": r.rule} for r in s.reasons],
                ready=s.ready,
                waiting_for=s.waiting_for,
            )
        )

    meta = {
        "model": result.model,
        "turns": result.turns,
        "duration_ms": result.duration_ms,
        "usage": asdict(result.usage),
    }
    # What the store could do when planned: lets the dashboard show the next step without a store call.
    case.plan = {"approach": result.output.approach, "available": sorted(available), **meta}
    case.status = CaseStatus.PLANNED
    summary = (
        {"action": best.action, "tier": str(best.tier), "expected_value": str(best.expected_value)} if best else None
    )
    case.events.append(CaseEvent(type="planned", actor="strategist", data={"recommended": summary, **meta}))


def _json_safe(params: dict[str, Any]) -> dict[str, Any]:
    return {k: str(v) if isinstance(v, Decimal) else v for k, v in params.items()}
