from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from revenueops import stats
from revenueops.cases.models import Case
from revenueops.db import get_session
from revenueops.executor.engine import act, approve
from revenueops.main import app
from revenueops.pipeline import detect
from tests.fakes import NOW, FakeStore, order
from tests.test_executor import p, planned, write


def test_overview_counts_open_recovered_and_how_actions_were_decided(session: Session) -> None:
    store = FakeStore(orders=[order("o1", hours_since_update=30, total="1000")])
    case = planned(session, store, p("send_confirmation_reminder", {"channel": "sms"}))
    act(session, store, write, [case], now=NOW)  # auto

    s = stats.overview(session, days=7, now=NOW)
    assert s["open"] == {"cases": 1, "value": "1000.00"}
    assert s["actions"]["auto"] == 1 and s["automation_rate"] == 1.0
    assert s["by_type"] == [{"case_type": "unconfirmed_order", "cases": 1, "value": "1000.00"}]
    assert s["by_governorate"] == [{"governorate": "Cairo", "cases": 1, "value": "1000.00"}]
    assert next(r for r in s["by_status"] if r["status"] == "acted")["cases"] == 1
    assert s["risk_scores"][2] == {"from": 20, "to": 29, "cases": 1}
    assert len(s["daily"]) == 7 and s["daily"][-1]["auto"] == 1

    store.orders = []  # the buyer confirmed after the reminder
    detect(session, store)
    s = stats.overview(session, days=7)
    assert s["open"]["cases"] == 0
    assert s["recovered"] == {"cases": 1, "value": "1000.00"}
    assert s["cleared_without_action"]["cases"] == 0


def test_cases_that_clear_with_no_action_are_not_counted_as_recovered(session: Session) -> None:
    store = FakeStore(orders=[order("o1", hours_since_update=30)])
    detect(session, store)
    store.orders = []
    detect(session, store)
    s = stats.overview(session, days=7)
    assert s["recovered"]["cases"] == 0 and s["cleared_without_action"]["cases"] == 1
    assert s["automation_rate"] is None


def test_approved_actions_and_the_api(session: Session) -> None:
    store = FakeStore(orders=[order("o1", total="20000", risk=90)])
    case = planned(session, store, p("hold_dispatch"))
    queued = act(session, store, write, [case], now=NOW).queued[0]
    assert stats.overview(session, days=7, now=NOW)["actions"]["pending_approval"] == 1
    approve(session, store, write, queued, by="omar", now=NOW)
    app.dependency_overrides[get_session] = lambda: session
    try:
        client = TestClient(app)
        body = client.get("/stats?days=7").json()
        policies = client.get("/policies").json()
    finally:
        app.dependency_overrides.clear()
    assert body["actions"]["approved"] == 1 and body["actions"]["pending_approval"] == 0
    cod = next(x for x in policies if x["id"] == "COD")
    assert cod["rules"][1]["id"] == "COD-2" and cod["rules"][1]["text"].startswith(
        "Orders with a store risk score of 55"
    )
    assert session.scalars(select(Case)).one().value_at_risk == 20000


def test_the_pipeline_runs_from_the_api(session: Session) -> None:
    from revenueops.config import Settings, get_settings
    from revenueops.main import get_llm, get_store, get_writer
    from tests.fakes import FakeLLM, tool_use

    store = FakeStore(orders=[order("o1", hours_since_update=30)])
    report = {
        "summary": "Pending, unanswered.",
        "likely_cause": "Missed the message.",
        "evidence": [{"fact": "Pending 30h", "source": "case_signals"}],
        "confidence": 0.8,
    }
    strategy = {
        "approach": "Remind.",
        "proposals": [{"action": "send_confirmation_reminder", "params": {"channel": "sms"}, "rationale": "r"}],
    }
    llm = FakeLLM([[tool_use("submit_investigation", report)], [tool_use("submit_strategy", strategy)]])
    key = {"X-Admin-Key": "k"}
    app.dependency_overrides.update(
        {
            get_session: lambda: session,
            get_store: lambda: store,
            get_llm: lambda: llm,
            get_writer: lambda: write,
            get_settings: lambda: Settings(_env_file=None, admin_api_key="k"),  # type: ignore[call-arg]
        }
    )
    try:
        client = TestClient(app)
        assert client.post("/detect", headers=key).json()["opened"] == 1
        case_id = session.scalars(select(Case)).one().id
        assert client.post(f"/cases/{case_id}/plan", headers=key).status_code == 409  # not investigated yet
        assert client.post(f"/cases/{case_id}/investigate", headers=key).json()["status"] == "investigated"
        assert client.post(f"/cases/{case_id}/plan", headers=key).json()["status"] == "planned"
        acted = client.post(f"/cases/{case_id}/act", headers=key).json()
        assert [e["status"] for e in acted["executions"]] == ["succeeded"]
        assert client.post("/detect").status_code == 401
    finally:
        app.dependency_overrides.clear()
