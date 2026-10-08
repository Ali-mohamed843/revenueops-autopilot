import json
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from revenueops.agents.investigator import InvestigationReport, investigate
from revenueops.agents.loop import AgentFailed
from revenueops.cases.models import Case, CaseStatus
from revenueops.commerce.models import CustomerProfile
from revenueops.pipeline import cases_to_investigate, detect, investigate_cases
from tests.fakes import FakeLLM, FakeStore, order, text, tool_use

REPORT: dict[str, Any] = {
    "summary": "Pending for 30 hours; the confirmation was never answered.",
    "likely_cause": "The buyer has not engaged since checkout.",
    "evidence": [
        {"fact": "Order total 1000 EGP, pending since checkout", "source": "order"},
        {"fact": "Customer refused 2 of 5 deliveries", "source": "customer_profile"},
    ],
    "risk_factors": ["Two past refusals"],
    "confidence": 0.7,
}


def store_with_case() -> FakeStore:
    return FakeStore(
        orders=[order("o1", hours_since_update=30)],
        profiles={"01099000001": CustomerProfile(key="01099000001", risk_score=48, refused_count=2)},
    )


def open_case(session: Session, store: FakeStore) -> Case:
    detect(session, store)
    (case,) = session.scalars(select(Case))
    return case


def happy_script() -> list[list[dict[str, Any]]]:
    return [
        [tool_use("get_order", {"order_id": "o1"})],
        [tool_use("get_customer_profile", {"customer_key": "01099000001"})],
        [text("Done."), tool_use("submit_investigation", REPORT)],
    ]


def test_investigates_with_tools_and_returns_a_validated_report(session: Session) -> None:
    store = store_with_case()
    llm = FakeLLM(happy_script())
    result = investigate(open_case(session, store), store, llm)

    assert isinstance(result.output, InvestigationReport)
    assert result.output.confidence == 0.7
    assert [c.name for c in result.tool_calls] == ["get_order", "get_customer_profile", "submit_investigation"]
    assert result.turns == 3 and result.usage.input_tokens == 300
    # The tool results the model saw are the store's real data.
    order_result = llm.requests[1]["messages"][-1]["content"][0]
    assert json.loads(order_result["content"])["id"] == "o1"
    # The prompt carries the case and the detector's signals.
    assert "unconfirmed_order" in llm.requests[0]["messages"][0]["content"]


def test_rejects_evidence_from_a_source_it_never_looked_at(session: Session) -> None:
    store = store_with_case()
    llm = FakeLLM(
        [
            [tool_use("get_order", {"order_id": "o1"})],
            [tool_use("submit_investigation", REPORT)],  # cites customer_profile without fetching it
            [tool_use("get_customer_profile", {"customer_key": "01099000001"})],
            [tool_use("submit_investigation", REPORT)],
        ]
    )
    result = investigate(open_case(session, store), store, llm)
    rejection = llm.requests[2]["messages"][-1]["content"][0]
    assert rejection["is_error"] and "customer_profile" in rejection["content"]
    assert result.turns == 4


def test_invalid_report_is_sent_back_then_fails_after_too_many_tries(session: Session) -> None:
    store = store_with_case()
    bad = {**REPORT, "confidence": 3}
    llm = FakeLLM([[tool_use("submit_investigation", bad)] for _ in range(3)])
    with pytest.raises(AgentFailed, match="rejected 3 times"):
        investigate(open_case(session, store), store, llm)
    assert "less than or equal to 1" in llm.requests[1]["messages"][-1]["content"][0]["content"]


def test_prose_answer_gets_a_nudge(session: Session) -> None:
    store = store_with_case()
    llm = FakeLLM([[text("I think it is fine.")], *happy_script()])
    result = investigate(open_case(session, store), store, llm)
    assert "submit_investigation" in llm.requests[1]["messages"][-1]["content"]
    assert result.turns == 4


def test_store_errors_reach_the_model_as_tool_errors(session: Session) -> None:
    store = store_with_case()
    llm = FakeLLM([[tool_use("get_order", {"order_id": "missing"})], *happy_script()])
    investigate(open_case(session, store), store, llm)
    first_result = llm.requests[1]["messages"][-1]["content"][0]
    assert first_result["is_error"] and "No order missing" in first_result["content"]


def test_gives_up_after_the_turn_budget(session: Session) -> None:
    store = store_with_case()
    llm = FakeLLM([[tool_use("get_order", {"order_id": "o1"})] for _ in range(8)])
    with pytest.raises(AgentFailed, match="after 8 turns"):
        investigate(open_case(session, store), store, llm)


def test_pipeline_stores_the_report_and_audit_trail(session: Session) -> None:
    store = store_with_case()
    case = open_case(session, store)
    run = investigate_cases(session, store, FakeLLM(happy_script()), [case])

    assert run.investigated == [case.id]
    session.refresh(case)
    assert case.status == CaseStatus.INVESTIGATED
    assert case.investigation is not None
    assert case.investigation["report"]["likely_cause"] == REPORT["likely_cause"]
    assert case.investigation["model"] == "fake-model"
    assert [e.type for e in case.events] == ["detected", "investigated"]
    assert case.events[-1].data["usage"] == {"input_tokens": 300, "output_tokens": 60}


def test_pipeline_marks_failures_retryable(session: Session) -> None:
    store = store_with_case()
    case = open_case(session, store)
    run = investigate_cases(session, store, FakeLLM([[text("no")], [text("no")]] * 4), [case])
    assert case.id in run.failed
    assert case.status == CaseStatus.INVESTIGATION_FAILED
    # A failed case is picked up again next time.
    assert cases_to_investigate(session, store.name, 10) == [case]


def test_queue_is_most_urgent_then_most_valuable(session: Session) -> None:
    store = FakeStore(
        orders=[
            order("small", hours_since_update=30, total="100"),
            order("big", hours_since_update=30, total="5000"),
            order("risky", risk=90, total="50"),
        ]
    )
    detect(session, store)
    queue = cases_to_investigate(session, store.name, 10)
    assert [c.subject_id for c in queue] == ["risky", "big", "small"]
    assert queue[1].value_at_risk == Decimal("5000.00")
