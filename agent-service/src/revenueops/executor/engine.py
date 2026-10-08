"""Carries out planned actions under the decision engine's rules.

The safety rules, each enforced here and tested:
- Only an action scored `auto` *at the moment it runs* runs without a person.
- `approval` actions wait for approve(); approve() scores them again and refuses anything that now
  needs a person to do it, or whose case has already been closed.
- `human_only` actions are never run by the service: a person does them and reports back.
- Each proposed action is carried out at most once (a unique key on executions.case_action_id).
- Every step is recorded on the case, with before/after snapshots on the execution.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, fields
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from revenueops.adapters.base import StoreAdapter, StoreError
from revenueops.agents.llm import LLMClient, LLMError
from revenueops.agents.loop import AgentFailed
from revenueops.agents.messenger import MessageBrief, draft_message
from revenueops.cases.models import ActionStatus, Case, CaseAction, CaseEvent, CaseStatus
from revenueops.commerce.models import Capabilities
from revenueops.decision.catalogue import CATALOGUE, actions_for
from revenueops.decision.score import CaseFacts, Score, Tier, rank, recommended, score
from revenueops.executor.handlers import HANDLERS, ActionFailed, Ctx, Writer
from revenueops.executor.models import OPEN_EXECUTION, Execution, ExecutionStatus, OutboxMessage, OutboxStatus

AUTHOR = "RevenueOps Autopilot"


class ExecutorError(Exception):
    """The executor refuses a request: wrong state, or a limit that now applies."""


def now_utc() -> datetime:
    return datetime.now(UTC)


def messenger_writer(llm: LLMClient) -> Writer:
    def write(brief: MessageBrief) -> tuple[str, str, str]:
        draft = draft_message(brief, llm).output
        return draft.language, draft.body, llm.model

    return write


def capabilities_of(store: StoreAdapter) -> set[str]:
    return {f.name for f in fields(Capabilities) if store.capabilities.has(f.name)}


def executions_of(session: Session, case: Case) -> list[Execution]:
    return list(session.scalars(select(Execution).where(Execution.case_id == case.id).order_by(Execution.requested_at)))


# ------------------------------------------------------------- choosing


def _facts(case: Case, executions: list[Execution], has: set[str], now: datetime) -> CaseFacts:
    done = {
        e.action_type: (now - _aware(e.finished_at)).total_seconds() / 3600
        for e in executions
        if e.status == ExecutionStatus.SUCCEEDED and e.finished_at is not None
    }
    return CaseFacts(
        case_type=case.case_type,
        value_at_risk=case.value_at_risk,
        confidence=(case.investigation or {}).get("report", {}).get("confidence"),
        done_hours_ago=done,
        available=frozenset(a.key for a in actions_for(case.case_type, has)),
    )


def _aware(at: datetime) -> datetime:
    return at if at.tzinfo else at.replace(tzinfo=UTC)  # SQLite drops the zone


def _rescore(action: CaseAction, facts: CaseFacts) -> Score:
    params = CATALOGUE[action.action_type].check_params(action.params, facts.value_at_risk)
    return score(action.action_type, params, facts)


def next_action(
    case: Case, executions: list[Execution], has: set[str], now: datetime
) -> tuple[CaseAction, Score] | None:
    """The best proposed action that is ready now and was never tried, scored afresh."""
    facts = _facts(case, executions, has, now)
    tried = {e.case_action_id for e in executions}
    candidates = [
        a
        for a in case.actions
        if a.status == ActionStatus.PROPOSED and a.id not in tried and a.action_type in (facts.available or ())
    ]
    scored = [(_rescore(a, facts), a) for a in candidates]
    best = recommended(rank([s for s, _ in scored]))
    if best is None:
        return None
    return next(a for s, a in scored if s is best), best


# -------------------------------------------------------------- acting


@dataclass
class ActRun:
    ran: list[uuid.UUID] = field(default_factory=list)  # executions that ran automatically
    queued: list[uuid.UUID] = field(default_factory=list)  # waiting for approval or a person
    failed: list[uuid.UUID] = field(default_factory=list)
    waiting: list[uuid.UUID] = field(default_factory=list)  # cases with nothing ready right now


def cases_to_act(session: Session, store: str, limit: int) -> list[Case]:
    return list(
        session.scalars(
            select(Case)
            .where(Case.store == store, Case.status.in_([CaseStatus.PLANNED, CaseStatus.ACTED]))
            .order_by(Case.priority, Case.value_at_risk.desc(), Case.detected_at)
            .limit(limit)
        )
    )


def act(session: Session, store: StoreAdapter, write: Writer, cases: list[Case], now: datetime | None = None) -> ActRun:
    """For each case, take the next ready action: run it if it is `auto`, otherwise queue it."""
    now = now or now_utc()
    run = ActRun()
    has = capabilities_of(store)
    for case in cases:
        executions = executions_of(session, case)
        if case.status == CaseStatus.CLOSED or any(e.status in OPEN_EXECUTION for e in executions):
            continue
        pick = next_action(case, executions, has, now)
        if pick is None:
            run.waiting.append(case.id)
            continue
        action, scored = pick
        ex = Execution(
            id=uuid.uuid4(),
            case=case,
            case_id=case.id,
            action=action,
            case_action_id=action.id,
            action_type=action.action_type,
            params=action.params,
            tier=str(scored.tier),
            status=ExecutionStatus.PENDING_APPROVAL,
            reversible=CATALOGUE[action.action_type].reversible,
            policy_refs=action.policy_refs,
            confidence=(case.investigation or {}).get("report", {}).get("confidence"),
            requested_at=now,
        )
        session.add(ex)
        _event(case, "action_requested", "executor", ex, now, {"tier": ex.tier})
        if scored.tier == Tier.AUTO:
            ex.decided_by, ex.decided_at = "auto", now
            _run(ex, store, write, now)
            (run.ran if ex.status == ExecutionStatus.SUCCEEDED else run.failed).append(ex.id)
        else:
            if scored.tier == Tier.HUMAN_ONLY:
                ex.status = ExecutionStatus.AWAITING_HUMAN
            case.status = CaseStatus.ACTING
            run.queued.append(ex.id)
        session.commit()
    return run


def _run(ex: Execution, store: StoreAdapter, write: Writer, now: datetime) -> None:
    handler = HANDLERS[ex.action_type]
    assert handler.run is not None, "actions done by a person never run here"
    case = ex.case
    params = CATALOGUE[ex.action_type].check_params(ex.params, case.value_at_risk)
    ctx = Ctx(case=case, params=params, store=store, write=write, now=now)
    try:
        outcome = handler.run(ctx)
    except (ActionFailed, StoreError, AgentFailed, LLMError) as e:
        ex.status, ex.error, ex.finished_at = ExecutionStatus.FAILED, str(e), now
        case.status = CaseStatus.PLANNED  # free to try the next action
        _event(case, "action_failed", "executor", ex, now, {"error": str(e)})
        return

    ex.before, ex.after, ex.result = outcome.before, outcome.after, outcome.result
    ex.status, ex.finished_at = ExecutionStatus.SUCCEEDED, now
    for m in outcome.messages:
        ex.messages.append(
            OutboxMessage(
                case_id=case.id,
                channel=m.channel,
                recipient=m.recipient,
                language=m.language,
                body=m.body,
                drafted_by=m.drafted_by,
                created_at=now,
            )
        )
    case.status = CaseStatus.ACTED
    _event(case, "action_succeeded", ex.decided_by or "executor", ex, now, {"messages": len(outcome.messages)})
    _note(ctx, f"{AUTHOR}: {CATALOGUE[ex.action_type].title} (case {case.id})", ex)


def _note(ctx: Ctx, text: str, ex: Execution) -> None:
    """Leave a trail in the store's admin panel. Best effort: the action itself already happened."""
    if ctx.case.subject_type != "order":
        return
    try:
        ctx.store.add_order_note(ctx.case.subject_id, text, AUTHOR)
    except StoreError as e:
        ex.result = {**(ex.result or {}), "note_error": str(e)}


def _event(case: Case, type_: str, actor: str, ex: Execution, now: datetime, extra: dict[str, Any]) -> None:
    case.events.append(
        CaseEvent(
            type=type_,
            actor=actor,
            at=now,
            data={"execution_id": str(ex.id), "action": ex.action_type, **extra},
        )
    )


# ---------------------------------------------------------- people's part


def _get(session: Session, execution_id: uuid.UUID) -> Execution:
    ex = session.get(Execution, execution_id)
    if ex is None:
        raise ExecutorError(f"No execution {execution_id}")
    return ex


def approve(
    session: Session,
    store: StoreAdapter,
    write: Writer,
    execution_id: uuid.UUID,
    by: str,
    note: str | None = None,
    now: datetime | None = None,
) -> Execution:
    now = now or now_utc()
    ex = _get(session, execution_id)
    if ex.status != ExecutionStatus.PENDING_APPROVAL:
        raise ExecutorError(f"Execution is {ex.status}, not waiting for approval")
    case = ex.case
    if case.status == CaseStatus.CLOSED:
        ex.status, ex.decided_by, ex.decided_at = ExecutionStatus.REJECTED, "system", now
        ex.decision_note = f"Case closed ({case.close_reason}) before approval"
        _event(case, "action_rejected", "system", ex, now, {"reason": ex.decision_note})
        session.commit()
        raise ExecutorError(ex.decision_note)
    # The limits are checked again now: the case may have changed since the action was queued.
    fresh = _rescore(ex.action, _facts(case, executions_of(session, case), capabilities_of(store), now))
    if fresh.tier == Tier.HUMAN_ONLY:
        reasons = "; ".join(r.text for r in fresh.reasons if r.tier == Tier.HUMAN_ONLY)
        raise ExecutorError(f"This now needs a person to do it: {reasons}")
    ex.decided_by, ex.decided_at, ex.decision_note = by, now, note
    _event(case, "action_approved", by, ex, now, {"note": note})
    _run(ex, store, write, now)
    session.commit()
    return ex


def reject(session: Session, execution_id: uuid.UUID, by: str, note: str | None = None) -> Execution:
    now = now_utc()
    ex = _get(session, execution_id)
    if ex.status not in OPEN_EXECUTION:
        raise ExecutorError(f"Execution is {ex.status}; only waiting actions can be rejected")
    ex.status, ex.decided_by, ex.decided_at, ex.decision_note = ExecutionStatus.REJECTED, by, now, note
    if ex.case.status != CaseStatus.CLOSED:
        ex.case.status = CaseStatus.PLANNED
    _event(ex.case, "action_rejected", by, ex, now, {"note": note})
    session.commit()
    return ex


def complete(session: Session, execution_id: uuid.UUID, by: str, succeeded: bool, note: str) -> Execution:
    """A person reports back on an action they carried out (a call, a cancellation)."""
    now = now_utc()
    ex = _get(session, execution_id)
    if ex.status != ExecutionStatus.AWAITING_HUMAN:
        raise ExecutorError(f"Execution is {ex.status}, not waiting for a person")
    ex.status = ExecutionStatus.SUCCEEDED if succeeded else ExecutionStatus.FAILED
    ex.decided_by, ex.decided_at, ex.finished_at = by, now, now
    ex.result = {"outcome": note}
    if ex.case.status != CaseStatus.CLOSED:
        ex.case.status = CaseStatus.ACTED if succeeded else CaseStatus.PLANNED
    _event(ex.case, "action_completed", by, ex, now, {"succeeded": succeeded, "note": note})
    session.commit()
    return ex


def rollback(
    session: Session, store: StoreAdapter, execution_id: uuid.UUID, by: str, reason: str, now: datetime | None = None
) -> Execution:
    """Undo an action: reverse its effect in the store and cancel its unsent messages."""
    now = now or now_utc()
    ex = _get(session, execution_id)
    if ex.status != ExecutionStatus.SUCCEEDED:
        raise ExecutorError(f"Execution is {ex.status}; only a succeeded action can be undone")
    handler = HANDLERS[ex.action_type]
    if handler.by_human:
        raise ExecutorError("A person carried this out; it can't be undone from here")
    if handler.changes_store and handler.undo is None:
        raise ExecutorError(f"{ex.action_type} can't be undone")
    if any(m.status != OutboxStatus.QUEUED for m in ex.messages):
        raise ExecutorError("A message was already sent or cancelled")

    undone: dict[str, Any] = {}
    ctx = Ctx(case=ex.case, params={}, store=store, write=_no_writer, now=now)
    if handler.undo is not None:
        try:
            undone = handler.undo(ctx, ex.result or {})
        except StoreError as e:
            raise ExecutorError(f"The store refused to undo it: {e}") from e
    for m in ex.messages:
        m.status, m.cancelled_at = OutboxStatus.CANCELLED, now
    ex.status, ex.rolled_back_by, ex.rolled_back_at = ExecutionStatus.ROLLED_BACK, by, now
    ex.rollback_result = {**undone, "messages_cancelled": len(ex.messages), "reason": reason}
    if ex.case.status != CaseStatus.CLOSED:
        ex.case.status = CaseStatus.PLANNED
    _event(ex.case, "action_rolled_back", by, ex, now, {"reason": reason, **undone})
    _note(ctx, f"{AUTHOR}: undid {CATALOGUE[ex.action_type].title} ({by}: {reason})", ex)
    session.commit()
    return ex


def _no_writer(brief: MessageBrief) -> tuple[str, str, str]:
    raise AssertionError("undo never drafts messages")
