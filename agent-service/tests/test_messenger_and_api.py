"""The Messenger's checks, and the action endpoints with their admin key."""

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from revenueops.agents.loop import AgentFailed
from revenueops.agents.messenger import MessageBrief, draft_message
from revenueops.cases.models import Case
from revenueops.config import Settings, get_settings
from revenueops.db import get_session
from revenueops.executor.engine import act
from revenueops.main import app, get_store, get_writer
from tests.fakes import NOW, FakeLLM, FakeStore, order, tool_use
from tests.test_executor import p, planned, write

BRIEF = MessageBrief(
    purpose="Remind the customer to confirm.",
    facts={"order reference": "#AB12CD34", "customer name": "Habiba"},
    must_include=("#AB12CD34",),
)


def submit(body: str, language: str = "ar") -> list[dict[str, Any]]:
    return [tool_use("submit_message", {"language": language, "body": body})]


# ---------------------------------------------------------------- messenger


def test_a_good_draft_is_accepted() -> None:
    llm = FakeLLM([submit("أهلاً حبيبة، من فضلك أكدي طلبك #AB12CD34 علشان نشحنه. شكراً!")])
    result = draft_message(BRIEF, llm)
    assert result.output.body.startswith("أهلاً حبيبة")
    prompt = llm.requests[0]["messages"][0]["content"]
    assert "Egyptian Arabic" in prompt and "Must include exactly: #AB12CD34" in prompt and "Habiba" in prompt


@pytest.mark.parametrize(
    ("bad", "message"),
    [
        (submit("أهلاً، من فضلك أكدي طلبك علشان نشحنه."), "must include exactly: #AB12CD34"),
        (submit("Please confirm order #AB12CD34. Thanks!", "en"), "write it in ar"),
        (submit("طلبك #AB12CD34 عليه تقييم مخاطر عالي، أكدي من فضلك."), "COMM-5"),
        (submit("Your risk score is high; confirm #AB12CD34", "ar"), "COMM-5"),
    ],
)
def test_bad_drafts_go_back_to_the_model(bad: list[dict[str, Any]], message: str) -> None:
    llm = FakeLLM([bad, submit("من فضلك أكدي طلبك #AB12CD34 علشان نشحنه. شكراً!")])
    draft_message(BRIEF, llm)
    assert message in llm.requests[1]["messages"][-1]["content"][0]["content"]


def test_a_draft_that_keeps_failing_fails_the_action() -> None:
    with pytest.raises(AgentFailed):
        draft_message(BRIEF, FakeLLM([submit("no reference here at all")] * 4))


# ---------------------------------------------------------------------- api


KEY = "test-admin-key"


@pytest.fixture
def api(session: Session) -> Iterator[tuple[TestClient, FakeStore, Case]]:
    store = FakeStore(orders=[order("o1", total="20000", risk=90)])
    case = planned(session, store, p("hold_dispatch"))
    act(session, store, write, [case], now=NOW)
    # Never the developer's .env: these tests set the key themselves.
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None, admin_api_key=KEY)  # type: ignore[call-arg]
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_writer] = lambda: write
    yield TestClient(app), store, case
    app.dependency_overrides.clear()


def test_queue_approve_and_rollback_through_the_api(api: tuple[TestClient, FakeStore, Case]) -> None:
    client, store, case = api
    (queued,) = client.get("/executions?status=pending_approval").json()
    assert (queued["action_type"], queued["tier"]) == ("hold_dispatch", "approval")

    approved = client.post(
        f"/executions/{queued['id']}/approve", json={"by": "omar"}, headers={"X-Admin-Key": KEY}
    ).json()
    assert (approved["status"], approved["decided_by"], approved["after"]["dispatch_hold"]) == (
        "succeeded",
        "omar",
        True,
    )

    detail = client.get(f"/cases/{case.id}").json()
    assert [e["status"] for e in detail["executions"]] == ["succeeded"]

    undone = client.post(
        f"/executions/{queued['id']}/rollback", json={"by": "omar", "reason": "Confirmed"}, headers={"X-Admin-Key": KEY}
    )
    assert undone.status_code == 200 and undone.json()["status"] == "rolled_back"
    again = client.post(
        f"/executions/{queued['id']}/rollback", json={"by": "omar", "reason": "x"}, headers={"X-Admin-Key": KEY}
    )
    assert again.status_code == 409 and "only a succeeded action" in again.json()["detail"]


def test_actions_need_the_admin_key(api: tuple[TestClient, FakeStore, Case]) -> None:
    client, store, _ = api
    (queued,) = client.get("/executions").json()
    url = f"/executions/{queued['id']}/approve"
    assert client.post(url, json={"by": "x"}).status_code == 401
    assert client.post(url, json={"by": "x"}, headers={"X-Admin-Key": "wrong"}).status_code == 401
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None, admin_api_key="")  # type: ignore[call-arg]
    assert client.post(url, json={"by": "x"}, headers={"X-Admin-Key": ""}).status_code == 503
    assert store.orders[0].shipment is not None and not store.orders[0].shipment.dispatch_hold  # nothing ran


def test_outbox_lists_drafted_messages(session: Session) -> None:
    store = FakeStore(orders=[order("o1", hours_since_update=30)])
    case = planned(session, store, p("send_confirmation_reminder", {"channel": "sms"}))
    act(session, store, write, [case], now=NOW)
    app.dependency_overrides[get_session] = lambda: session
    try:
        (message,) = TestClient(app).get("/outbox?status=queued").json()
    finally:
        app.dependency_overrides.clear()
    assert (message["channel"], message["recipient"], message["case_id"]) == ("sms", "01099000001", str(case.id))
    assert session.scalars(select(Case)).one().status == "acted"
