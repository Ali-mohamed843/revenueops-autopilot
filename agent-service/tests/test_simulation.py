"""The simulated world, outcome runs, calibration, and the dry run."""

import random
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from revenueops.cases.models import Case
from revenueops.config import Settings, get_settings
from revenueops.db import get_session
from revenueops.decision.rates import NO_ACTION
from revenueops.executor.engine import act
from revenueops.main import app, get_store
from revenueops.pipeline import detect
from revenueops.simulation import outcomes as sim
from revenueops.simulation import world
from revenueops.simulation.dry_run import dry_run
from revenueops.simulation.models import Outcome, RunKind, SimulationRun
from tests.fakes import NOW, FakeStore, cart, order
from tests.test_executor import p, planned, write

# ------------------------------------------------------------------ world


def test_segments_from_risk_and_size() -> None:
    assert world.segment("refusal_risk", Decimal(20000), {"risk_score": 70}) == "high-risk · large"
    assert world.segment("unconfirmed_order", Decimal(5000), {"risk_score": 40}) == "medium-risk · mid"
    assert world.segment("abandoned_cart", Decimal(500), {}) == "low-risk · small"


def test_true_chances_differ_by_segment() -> None:
    base = world.true_chance("unconfirmed_order", NO_ACTION, "low-risk · small")
    assert base == 0.18
    low = world.true_chance("unconfirmed_order", "send_confirmation_reminder", "low-risk · small")
    high = world.true_chance("unconfirmed_order", "send_confirmation_reminder", "high-risk · small")
    assert low > high > base  # reminders work less on high-risk customers
    call_high = world.true_chance("refusal_risk", "request_phone_confirmation", "high-risk · mid")
    call_low = world.true_chance("refusal_risk", "request_phone_confirmation", "low-risk · mid")
    assert call_high > call_low  # calls work better on them
    big = world.true_chance("abandoned_cart", "offer_discount", "low-risk · large")
    small = world.true_chance("abandoned_cart", "offer_discount", "low-risk · small")
    assert big > small
    assert world.true_chance("late_shipment", "hold_dispatch", "low-risk · mid") == world.TRUE_BASELINE["late_shipment"]


def test_draws_are_reproducible() -> None:
    a = [world.draw(random.Random(7), "refusal_risk", "request_deposit", "high-risk · large") for _ in range(5)]
    b = [world.draw(random.Random(7), "refusal_risk", "request_deposit", "high-risk · large") for _ in range(5)]
    assert a == b


# --------------------------------------------------------------- outcomes


def seeded(session: Session) -> None:
    detect(
        session,
        FakeStore(
            orders=[order("o1", hours_since_update=30, total="1000"), order("o2", risk=80, total="20000")],
            carts=[cart("c1", idle_hours=48, value="800")],
        ),
    )


def test_simulating_needs_cases_and_episodes(session: Session) -> None:
    with pytest.raises(sim.SimulationError, match="scan the store"):
        sim.simulate_outcomes(session, 100, seed=1)
    seeded(session)
    with pytest.raises(sim.SimulationError, match="at least one"):
        sim.simulate_outcomes(session, 0, seed=1)


def test_an_outcomes_run_is_randomised_reproducible_and_measured(session: Session) -> None:
    seeded(session)
    run = sim.simulate_outcomes(session, 3000, seed=7)
    assert run.kind == RunKind.OUTCOMES and run.report["episodes"] == 3000
    n = session.scalar(select(func.count()).select_from(Outcome).where(Outcome.run_id == run.id))
    assert n == 3000
    # The control group exists for every case type in the mix.
    actions = {a for (a,) in session.execute(select(Outcome.action).where(Outcome.run_id == run.id).distinct())}
    assert NO_ACTION in actions and "send_confirmation_reminder" in actions

    table = sim.measure(session, run.id)
    reminder = table.rates[("send_confirmation_reminder", "unconfirmed_order")]
    truth = world.true_chance("unconfirmed_order", "send_confirmation_reminder", "low-risk · small")
    # The measurement recovers the hidden truth. A 95% interval misses 1 seed in 20 (checked: 93.9%
    # coverage over 40 seeds, mean error +0.0005), so a fixed-seed test uses the 99.9% interval.
    lo, hi = reminder.interval(z=3.29)
    assert lo <= truth <= hi

    again = sim.simulate_outcomes(session, 3000, seed=7)
    assert again.report["recovered"] == run.report["recovered"]

    row = next(
        r
        for r in run.report["comparison"]
        if r["action"] == "send_confirmation_reminder" and r["case_type"] == "unconfirmed_order"
    )
    assert row["assumed"] == 0.45 and row["enough"] and row["trials"] == reminder.trials
    missing = next(r for r in run.report["comparison"] if r["case_type"] == "stale_return")
    assert missing["measured"] is None and not missing["enough"]  # no returns in this store's mix


def test_segments_are_reported(session: Session) -> None:
    seeded(session)
    run = sim.simulate_outcomes(session, 500, seed=3)
    rows = sim.by_segment(session, run.id)
    assert {"high-risk · large", "low-risk · small"} <= {r["segment"] for r in rows}
    assert sum(r["trials"] for r in rows) == 500


def test_calibration_feeds_planning_and_acting(session: Session) -> None:
    with pytest.raises(sim.SimulationError, match="simulate outcomes first"):
        sim.calibrate(session)
    assert sim.active_rates(session) == (None, None)

    store = FakeStore(orders=[order("o1", hours_since_update=30)])
    detect(session, store)
    sim.simulate_outcomes(session, 4000, seed=11)
    cal = sim.calibrate(session)
    assert cal.kind == RunKind.CALIBRATION and cal.report["usable"] > 0
    rates, run = sim.active_rates(session)
    assert run is not None and rates is not None and rates.source.startswith(f"calibration {str(cal.id)[:8]}")

    # A case planned now is scored with the measured rates, and says so.
    session.execute(Case.__table__.delete())  # type: ignore[attr-defined]
    session.commit()
    case = planned(session, store, p("send_confirmation_reminder", {"channel": "sms"}))
    action = case.actions[0]
    measured = rates.get("send_confirmation_reminder", "unconfirmed_order")
    assert measured is not None
    assert action.measured and action.trials == measured.trials and action.p_with == measured.p
    assert case.plan is not None and case.plan["rates"] == rates.source
    run_ = act(session, store, write, [case], now=NOW)
    assert run_.ran  # still auto; the executor scored it with the same rates


def test_calibrating_a_named_run_and_refusing_the_wrong_kind(session: Session) -> None:
    seeded(session)
    first = sim.simulate_outcomes(session, 200, seed=1)
    sim.simulate_outcomes(session, 200, seed=2)
    cal = sim.calibrate(session, first.id)
    assert cal.params["outcomes_run"] == str(first.id)
    with pytest.raises(sim.SimulationError):
        sim.calibrate(session, cal.id)


# ---------------------------------------------------------------- dry run


def test_dry_run_reports_what_would_happen_and_changes_nothing(session: Session) -> None:
    store = FakeStore(
        orders=[order("o1", hours_since_update=30, total="1000"), order("o2", total="20000", risk=90)],
        carts=[cart("c1", idle_hours=48, value="800")],
    )
    case = planned_first(session, store)
    notes, held = list(store.notes), store.orders[1].shipment.dispatch_hold  # type: ignore[union-attr]

    run = dry_run(session, store, rates=None, now=NOW)
    r: dict[str, Any] = run.report
    assert run.kind == RunKind.DRY_RUN and run.params["rates"] == "starting estimates"
    assert r["cases"] == 3
    assert r["planned_by"] == {"plan": 1, "catalogue": 2}
    # o1 has a plan (reminder: auto); o2 is refusal risk on a large order; the cart gets a reminder.
    assert r["tiers"]["auto"] >= 2
    assert r["actions"]["send_confirmation_reminder"] >= 1 and r["actions"]["send_cart_reminder"] == 1
    assert Decimal(r["expected_recovery"]) > 0
    assert all(h["tier"] != "auto" for h in r["high_risk"])
    assert r["approvals_needed"] == r["tiers"]["approval"] + r["tiers"]["human_only"]
    # Nothing touched the store, and no execution was created.
    assert store.notes == notes and store.orders[1].shipment.dispatch_hold == held  # type: ignore[union-attr]
    assert case.status == "planned"


def planned_first(session: Session, store: FakeStore) -> Case:
    o1 = FakeStore(orders=[store.orders[0]])
    return planned(session, o1, p("send_confirmation_reminder", {"channel": "sms"}))


def test_dry_run_counts_cases_already_waiting(session: Session) -> None:
    store = FakeStore(orders=[order("o1", total="20000", risk=90)])
    case = planned(session, store, p("hold_dispatch"))
    act(session, store, write, [case], now=NOW)  # queued for approval
    r = dry_run(session, store, rates=None, now=NOW).report
    assert r["tiers"]["waiting"] == 1 and r["cases"] == 1


def test_dry_run_counts_cases_with_nothing_ready(session: Session) -> None:
    store = FakeStore(orders=[order("o1", hours_since_update=30)])
    case = planned(session, store, p("send_confirmation_reminder", {"channel": "sms"}))
    act(session, store, write, [case], now=NOW)  # its only planned action has run
    r = dry_run(session, store, rates=None, now=NOW).report
    assert r["tiers"]["nothing_ready"] == 1 and r["expected_recovery"] == "0.00"


# --------------------------------------------------------------------- api


def test_simulation_api(session: Session) -> None:
    store = FakeStore(orders=[order("o1", hours_since_update=30)])
    detect(session, store)
    app.dependency_overrides.update(
        {
            get_session: lambda: session,
            get_store: lambda: store,
            get_settings: lambda: Settings(_env_file=None, admin_api_key="k"),  # type: ignore[call-arg]
        }
    )
    key = {"X-Admin-Key": "k"}
    try:
        c = TestClient(app)
        assert c.get("/simulation").json() == {
            "dry_run": None,
            "outcomes": None,
            "calibration": None,
            "rates_in_use": "starting estimates",
        }
        assert c.post("/simulation/calibrate", headers=key).status_code == 409
        assert c.post("/simulation/outcomes", json={"episodes": 500, "seed": 3}).status_code == 401
        out = c.post("/simulation/outcomes", json={"episodes": 500, "seed": 3}, headers=key).json()
        assert out["kind"] == "outcomes" and out["report"]["episodes"] == 500
        assert c.post("/simulation/calibrate", headers=key).json()["kind"] == "calibration"
        dry = c.post("/simulation/dry-run", headers=key).json()
        assert dry["report"]["cases"] == 1 and dry["params"]["rates"].startswith("calibration")
        overview = c.get("/simulation").json()
        assert overview["rates_in_use"].startswith("calibration") and overview["dry_run"]["id"] == dry["id"]
        assert c.post("/simulation/outcomes", json={"episodes": 5}, headers=key).status_code == 422
    finally:
        app.dependency_overrides.clear()
    assert session.scalar(select(func.count()).select_from(SimulationRun)) == 3
