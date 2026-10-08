import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Query, Response, status
from pydantic import BaseModel
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from revenueops import __version__
from revenueops.cases.models import ActionStatus, Case, CaseStatus, CaseType
from revenueops.db import database_is_up, get_engine, get_session

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
    status: str


class CaseDetailOut(CaseOut):
    investigation: dict[str, Any] | None
    plan: dict[str, Any] | None
    actions: list[CaseActionOut]  # the current plan, best first
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
            "events": [{"type": e.type, "actor": e.actor, "data": e.data, "at": e.at} for e in case.events],
        }
    )
