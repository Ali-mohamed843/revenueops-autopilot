import secrets
import uuid
from collections.abc import Iterator
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from revenueops import __version__, pipeline, stats
from revenueops.adapters.base import StoreAdapter
from revenueops.adapters.registry import build_adapter
from revenueops.agents.llm import LLMClient, build_llm
from revenueops.cases.models import ActionStatus, Case, CaseStatus, CaseType
from revenueops.config import Settings, get_settings
from revenueops.db import database_is_up, get_engine, get_session
from revenueops.executor import engine
from revenueops.executor.handlers import Writer
from revenueops.executor.models import Execution, ExecutionStatus, OutboxMessage, OutboxStatus
from revenueops.policies import all_policies

app = FastAPI(title="RevenueOps Autopilot", version=__version__)

SessionDep = Annotated[Session, Depends(get_session)]


@app.get("/health")
def health(response: Response, engine: Annotated[Engine, Depends(get_engine)]) -> dict[str, str]:
    """Liveness plus a database check; 503 when the database is unreachable."""
    if database_is_up(engine):
        return {"status": "ok", "database": "ok"}
    response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "degraded", "database": "unavailable"}


class CaseOut(BaseModel):
    id: uuid.UUID
    store: str
    case_type: str
    subject_type: str
    subject_id: str
    status: str
    title: str
    value_at_risk: Decimal
    currency: str
    priority: int
    signals: dict[str, Any]
    detected_at: datetime
    last_seen_at: datetime
    closed_at: datetime | None
    close_reason: str | None


class CaseEventOut(BaseModel):
    type: str
    actor: str
    data: dict[str, Any]
    at: datetime


class CaseActionOut(BaseModel):
    id: uuid.UUID
    rank: int
    recommended: bool
    action_type: str
    params: dict[str, Any]
    rationale: str
    policy_refs: list[str]
    p_with: float
    p_without: float
    cost: Decimal
    expected_value: Decimal
    tier: str
    tier_reasons: list[dict[str, Any]]
    ready: bool
    waiting_for: str | None
    status: str


class OutboxOut(BaseModel):
    id: uuid.UUID
    execution_id: uuid.UUID
    case_id: uuid.UUID
    channel: str
    recipient: str
    language: str
    body: str
    drafted_by: str
    status: str
    created_at: datetime
    cancelled_at: datetime | None


class ExecutionOut(BaseModel):
    id: uuid.UUID
    case_id: uuid.UUID
    case_action_id: uuid.UUID
    action_type: str
    params: dict[str, Any]
    tier: str
    status: str
    reversible: bool
    policy_refs: list[str]
    confidence: float | None
    requested_at: datetime
    decided_by: str | None
    decided_at: datetime | None
    decision_note: str | None
    finished_at: datetime | None
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    result: dict[str, Any] | None
    error: str | None
    rolled_back_by: str | None
    rolled_back_at: datetime | None
    rollback_result: dict[str, Any] | None
    messages: list[OutboxOut]


class CaseDetailOut(CaseOut):
    investigation: dict[str, Any] | None
    plan: dict[str, Any] | None
    actions: list[CaseActionOut]  # the current plan, best first
    next_action_id: uuid.UUID | None  # what `act` would take now, if anything
    executions: list[ExecutionOut]
    events: list[CaseEventOut]


def _case_out(c: Case) -> dict[str, Any]:
    return {k: getattr(c, k) for k in CaseOut.model_fields}


@app.get("/cases")
def list_cases(
    session: SessionDep,
    status_: Annotated[list[CaseStatus] | None, Query(alias="status")] = None,
    case_type: Annotated[list[CaseType] | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[CaseOut]:
    """Cases, most urgent first."""
    query = select(Case).order_by(Case.priority, Case.value_at_risk.desc(), Case.detected_at).limit(limit)
    if status_:
        query = query.where(Case.status.in_(status_))
    if case_type:
        query = query.where(Case.case_type.in_(case_type))
    return [CaseOut.model_validate(_case_out(c)) for c in session.scalars(query)]


@app.get("/cases/{case_id}")
def get_case(case_id: uuid.UUID, session: SessionDep) -> CaseDetailOut:
    case = session.get(Case, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")
    executions = engine.executions_of(session, case)
    next_action = engine.peek_next_action(case, executions)
    return CaseDetailOut.model_validate(
        {
            **_case_out(case),
            "investigation": case.investigation,
            "plan": case.plan,
            "actions": [
                {k: getattr(a, k) for k in CaseActionOut.model_fields}
                for a in case.actions
                if a.status == ActionStatus.PROPOSED
            ],
            "executions": [_execution_out(e) for e in executions],
            "next_action_id": next_action.id if next_action else None,
            "events": [{"type": e.type, "actor": e.actor, "data": e.data, "at": e.at} for e in case.events],
        }
    )


# ---------------------------------------------------------------- actions


def _outbox_out(m: OutboxMessage) -> OutboxOut:
    return OutboxOut.model_validate({k: getattr(m, k) for k in OutboxOut.model_fields})


def _execution_out(e: Execution) -> ExecutionOut:
    fields = {k: getattr(e, k) for k in ExecutionOut.model_fields if k != "messages"}
    return ExecutionOut.model_validate({**fields, "messages": [_outbox_out(m) for m in e.messages]})


SettingsDep = Annotated[Settings, Depends(get_settings)]


def require_admin(settings: SettingsDep, x_admin_key: Annotated[str | None, Header()] = None) -> None:
    expected = settings.admin_api_key
    if not expected:
        raise HTTPException(status_code=503, detail="Set ADMIN_API_KEY to enable actions")
    if not x_admin_key or not secrets.compare_digest(x_admin_key, expected):
        raise HTTPException(status_code=401, detail="Invalid or missing X-Admin-Key")


def get_store() -> Iterator[StoreAdapter]:
    yield build_adapter(get_settings())


def get_llm() -> LLMClient:
    return build_llm(get_settings())


def get_writer(llm: Annotated[LLMClient, Depends(get_llm)]) -> Writer:
    return engine.messenger_writer(llm)


AdminDep = Annotated[None, Depends(require_admin)]
StoreDep = Annotated[StoreAdapter, Depends(get_store)]
LLMDep = Annotated[LLMClient, Depends(get_llm)]
WriterDep = Annotated[Writer, Depends(get_writer)]


class Decision(BaseModel):
    by: str = Field(min_length=1, max_length=100, description="Who decides, for the audit trail")
    note: str | None = Field(default=None, max_length=2000)


class Completion(BaseModel):
    by: str = Field(min_length=1, max_length=100)
    succeeded: bool
    note: str = Field(min_length=1, max_length=2000, description="What happened, e.g. the call's outcome")


class Rollback(BaseModel):
    by: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=1, max_length=2000)


def _refused(e: engine.ExecutorError) -> HTTPException:
    return HTTPException(status_code=409, detail=str(e))


@app.get("/executions")
def list_executions(
    session: SessionDep,
    status_: Annotated[list[ExecutionStatus] | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[ExecutionOut]:
    """Actions taken or waiting, newest first. `?status=pending_approval` is the approval queue."""
    query = select(Execution).order_by(Execution.requested_at.desc()).limit(limit)
    if status_:
        query = query.where(Execution.status.in_(status_))
    return [_execution_out(e) for e in session.scalars(query)]


@app.get("/outbox")
def list_outbox(
    session: SessionDep,
    status_: Annotated[list[OutboxStatus] | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[OutboxOut]:
    """Drafted messages, newest first. Nothing is sent in this version."""
    query = select(OutboxMessage).order_by(OutboxMessage.created_at.desc()).limit(limit)
    if status_:
        query = query.where(OutboxMessage.status.in_(status_))
    return [_outbox_out(m) for m in session.scalars(query)]


@app.post("/executions/{execution_id}/approve")
def approve_execution(
    execution_id: uuid.UUID, body: Decision, session: SessionDep, store: StoreDep, write: WriterDep, _: AdminDep
) -> ExecutionOut:
    try:
        return _execution_out(engine.approve(session, store, write, execution_id, body.by, body.note))
    except engine.ExecutorError as e:
        raise _refused(e) from e


@app.post("/executions/{execution_id}/reject")
def reject_execution(execution_id: uuid.UUID, body: Decision, session: SessionDep, _: AdminDep) -> ExecutionOut:
    try:
        return _execution_out(engine.reject(session, execution_id, body.by, body.note))
    except engine.ExecutorError as e:
        raise _refused(e) from e


@app.post("/executions/{execution_id}/complete")
def complete_execution(execution_id: uuid.UUID, body: Completion, session: SessionDep, _: AdminDep) -> ExecutionOut:
    """Report back on an action a person carried out, such as a confirmation call."""
    try:
        return _execution_out(engine.complete(session, execution_id, body.by, body.succeeded, body.note))
    except engine.ExecutorError as e:
        raise _refused(e) from e


@app.post("/executions/{execution_id}/rollback")
def rollback_execution(
    execution_id: uuid.UUID, body: Rollback, session: SessionDep, store: StoreDep, _: AdminDep
) -> ExecutionOut:
    try:
        return _execution_out(engine.rollback(session, store, execution_id, body.by, body.reason))
    except engine.ExecutorError as e:
        raise _refused(e) from e


# ---------------------------------------------------------------- dashboard


@app.get("/stats")
def get_stats(session: SessionDep, days: Annotated[int, Query(ge=1, le=365)] = 30) -> dict[str, Any]:
    """Headline numbers and chart series for the dashboard, over the last `days` days."""
    return stats.overview(session, days)


class RuleOut(BaseModel):
    id: str
    text: str


class PolicyOut(BaseModel):
    id: str
    title: str
    applies_to: list[str]
    rules: list[RuleOut]


@app.get("/policies")
def list_policies() -> list[PolicyOut]:
    return [
        PolicyOut(
            id=p.id,
            title=p.title,
            applies_to=list(p.applies_to),
            rules=[RuleOut(id=i, text=t) for i, t in p.rule_texts().items()],
        )
        for p in all_policies()
    ]


# ------------------------------------------------- running the pipeline


def _case(session: Session, case_id: uuid.UUID) -> Case:
    case = session.get(Case, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")
    return case


@app.post("/detect")
def run_detect(session: SessionDep, store: StoreDep, _: AdminDep) -> dict[str, Any]:
    """Scan the store now: open, update or close cases."""
    run = pipeline.detect(session, store)
    s = run.sync
    return {
        "found": len(run.result.candidates),
        "opened": s.opened,
        "updated": s.updated,
        "reclassified": s.reclassified,
        "closed": s.closed,
    }


@app.post("/cases/{case_id}/investigate")
def run_investigate(case_id: uuid.UUID, session: SessionDep, store: StoreDep, llm: LLMDep, _: AdminDep) -> CaseOut:
    case = _case(session, case_id)
    if case.status == CaseStatus.CLOSED:
        raise HTTPException(status_code=409, detail="The case is closed")
    run = pipeline.investigate_cases(session, store, llm, [case])
    if run.failed:
        raise HTTPException(status_code=502, detail=run.failed[case.id])
    return CaseOut.model_validate(_case_out(case))


@app.post("/cases/{case_id}/plan")
def run_plan(case_id: uuid.UUID, session: SessionDep, store: StoreDep, llm: LLMDep, _: AdminDep) -> CaseOut:
    case = _case(session, case_id)
    if not case.investigation or case.status == CaseStatus.CLOSED:
        raise HTTPException(status_code=409, detail="Investigate the case before planning it")
    run = pipeline.plan_cases(session, store, llm, [case])
    if run.failed:
        raise HTTPException(status_code=502, detail=run.failed[case.id])
    return CaseOut.model_validate(_case_out(case))


@app.post("/cases/{case_id}/act")
def run_act(case_id: uuid.UUID, session: SessionDep, store: StoreDep, write: WriterDep, _: AdminDep) -> dict[str, Any]:
    """Take the case's next ready action: run it if it is auto, otherwise queue it for a person."""
    case = _case(session, case_id)
    if case.status not in (CaseStatus.PLANNED, CaseStatus.ACTED):
        raise HTTPException(status_code=409, detail=f"The case is {case.status}; only planned cases can act")
    run = engine.act(session, store, write, [case])
    ids = run.ran + run.queued + run.failed
    return {
        "executions": [_execution_out(e) for e in session.scalars(select(Execution).where(Execution.id.in_(ids)))],
        "waiting": bool(run.waiting),
    }
