from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from revenueops.agents.loop import AgentFailed
from revenueops.agents.strategist import plan
from revenueops.cases.models import ActionStatus, Case, CaseStatus
from revenueops.db import get_session
from revenueops.decision.catalogue import actions_for
from revenueops.main import app
from revenueops.pipeline import cases_to_plan, detect, plan_cases
from revenueops.policies import policies_for
from tests.fakes import FakeLLM, FakeStore, order, tool_use

ALL = {"order_confirmations", "delivery_tracking", "cod_risk_scores", "abandoned_carts", "returns"}

REMINDER = {
    "action": "send_confirmation_reminder",
    "params": {"channel": "whatsapp"},
    "rationale": "Reliable buyer; the confirmation simply expired.",
    "policy_refs": ["COD-1", "COMM-1"],
}
PHONE = {
    "action": "request_phone_confirmation",
    "params": {},
    "rationale": "A call settles it for a large order.",
    "policy_refs": ["COD-2"],
}
CANCEL = {"action": "recommend_cancellation", "params": {}, "rationale": "Fallback.", "policy_refs": ["COD-4"]}


def strategy(*proposals: dict[str, Any]) -> dict[str, Any]:
    return {"approach": "Re-confirm before anything else.", "proposals": list(proposals)}


def investigated_case(session: Session, confidence: float = 0.85, total: str = "1000") -> Case:
    detect(session, FakeStore(orders=[order("o1", hours_since_update=30, total=total)]))
    case = session.scalars(select(Case)).one()
    case.investigation = {"report": {"summary": "Expired confirmation.", "confidence": confidence}}
    case.status = CaseStatus.INVESTIGATED
    session.commit()
    return case


def run_plan(case: Case, llm: FakeLLM):  # type: ignore[no-untyped-def]
    ct = case.case_type
    return plan(case, actions_for(ct, ALL), policies_for(ct), llm)


# ------------------------------------------------------------------- the agent


def test_prompt_lists_only_the_case_actions_and_its_policies(session: Session) -> None:
    case = investigated_case(session)
    llm = FakeLLM([[tool_use("submit_strategy", strategy(REMINDER))]])
    run_plan(case, llm)
    prompt = llm.requests[0]["messages"][0]["content"]
    assert "send_confirmation_reminder" in prompt and "channel (whatsapp | sms)" in prompt
    assert "offer_discount" not in prompt  # not for unconfirmed orders
    assert "COD-2" in prompt and "RET-2" not in prompt
    assert "Expired confirmation." in prompt
    assert [t["name"] for t in llm.requests[0]["tools"]] == ["submit_strategy"]


@pytest.mark.parametrize(
    ("bad", "message"),
    [
        ({**REMINDER, "action": "offer_discount"}, "not an available action"),
        ({**REMINDER, "action": "wire_money"}, "not an available action"),
        ({**REMINDER, "params": {"channel": "pigeon"}}, "channel must be one of"),
        ({**REMINDER, "policy_refs": ["COD-99"]}, "unknown rules COD-99"),
    ],
)
def test_invalid_proposals_go_back_to_the_model(session: Session, bad: dict[str, Any], message: str) -> None:
    case = investigated_case(session)
    llm = FakeLLM([[tool_use("submit_strategy", strategy(bad))], [tool_use("submit_strategy", strategy(REMINDER))]])
    result = run_plan(case, llm)
    rejection = llm.requests[1]["messages"][-1]["content"][0]
    assert rejection["is_error"] and message in rejection["content"]
    assert result.output.proposals[0].action == "send_confirmation_reminder"


def test_duplicate_proposals_are_rejected(session: Session) -> None:
    case = investigated_case(session)
    llm = FakeLLM([[tool_use("submit_strategy", strategy(REMINDER, REMINDER))]] * 3)
    with pytest.raises(AgentFailed):
        run_plan(case, llm)
    assert "proposed twice" in llm.requests[1]["messages"][-1]["content"][0]["content"]


# ------------------------------------------------------------- the pipeline


def test_plan_is_scored_ranked_and_stored(session: Session) -> None:
    case = investigated_case(session)
    llm = FakeLLM([[tool_use("submit_strategy", strategy(CANCEL, REMINDER, PHONE))]])
    run = plan_cases(session, FakeStore(), llm, [case])

    assert run.planned == [case.id]
    assert case.status == CaseStatus.PLANNED
    assert case.plan is not None and case.plan["approach"] == "Re-confirm before anything else."
    # Ranked by the scorer, not by the model's order.
    assert [(a.rank, a.action_type, a.tier, a.recommended) for a in case.actions] == [
        (1, "request_phone_confirmation", "human_only", True),
        (2, "send_confirmation_reminder", "auto", False),
        (3, "recommend_cancellation", "human_only", False),
    ]
    reminder = case.actions[1]
    assert reminder.expected_value == Decimal("199.50") and reminder.policy_refs == ["COD-1", "COMM-1"]
    assert reminder.tier_reasons == [
        {"tier": "auto", "text": "Within every limit, and harmless if wrong", "rule": None}
    ]
    planned = case.events[-1]
    assert planned.type == "planned"
    assert planned.data["recommended"] == {
        "action": "request_phone_confirmation",
        "tier": "human_only",
        "expected_value": "292.00",
    }


def test_low_confidence_investigations_push_actions_to_approval(session: Session) -> None:
    case = investigated_case(session, confidence=0.4)
    plan_cases(session, FakeStore(), FakeLLM([[tool_use("submit_strategy", strategy(REMINDER))]]), [case])
    assert case.actions[0].tier == "approval"


def test_replanning_supersedes_the_old_plan(session: Session) -> None:
    case = investigated_case(session)
    plan_cases(session, FakeStore(), FakeLLM([[tool_use("submit_strategy", strategy(REMINDER))]]), [case])
    case.status = CaseStatus.PLANNING_FAILED  # e.g. a retry
    plan_cases(session, FakeStore(), FakeLLM([[tool_use("submit_strategy", strategy(PHONE))]]), [case])
    assert [(a.action_type, a.status) for a in case.actions] == [
        ("send_confirmation_reminder", ActionStatus.SUPERSEDED),
        ("request_phone_confirmation", ActionStatus.PROPOSED),
    ]


def test_failures_are_retryable_and_only_investigated_cases_are_planned(session: Session) -> None:
    case = investigated_case(session)
    run = plan_cases(session, FakeStore(), FakeLLM([[{"type": "text", "text": "no"}]] * 4), [case])
    assert case.id in run.failed and case.status == CaseStatus.PLANNING_FAILED
    assert cases_to_plan(session, "fake", 10) == [case]
    case.status = CaseStatus.OPEN
    assert cases_to_plan(session, "fake", 10) == []


def test_a_case_with_no_fitting_action_fails_without_calling_the_model(session: Session) -> None:
    case = investigated_case(session)
    case.case_type = "stale_return"
    store = FakeStore()
    store.capabilities = type(store.capabilities)()  # no `returns` capability: nothing can process it
    llm = FakeLLM([])
    run = plan_cases(session, store, llm, [case])
    assert "No catalogue action fits stale_return" in run.failed[case.id]
    assert llm.requests == []


def test_case_detail_api_shows_the_current_plan(session: Session) -> None:
    case = investigated_case(session)
    plan_cases(session, FakeStore(), FakeLLM([[tool_use("submit_strategy", strategy(REMINDER, PHONE))]]), [case])
    app.dependency_overrides[get_session] = lambda: session
    try:
        body = TestClient(app).get(f"/cases/{case.id}").json()
    finally:
        app.dependency_overrides.clear()
    assert body["status"] == "planned"
    assert [(a["action_type"], a["tier"], a["expected_value"]) for a in body["actions"]] == [
        ("request_phone_confirmation", "human_only", "292.00"),
        ("send_confirmation_reminder", "auto", "199.50"),
    ]
    assert body["plan"]["approach"] == "Re-confirm before anything else."
