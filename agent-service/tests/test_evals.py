"""The eval set is valid, the engine alone never runs a person's decision alone, and judging is right."""

from typing import Any

from revenueops.decision.catalogue import CATALOGUE
from revenueops.decision.score import Tier
from revenueops.evals.run import NONE, Result, evaluate, judge, load_scenarios, markdown, person_required
from tests.fakes import FakeLLM, tool_use


def test_the_dataset_is_well_formed() -> None:
    scenarios = load_scenarios()
    assert len(scenarios) == 40 and len({s["id"] for s in scenarios}) == 40
    for s in scenarios:
        for action, tiers in s["acceptable"].items():
            if action == NONE:
                assert tiers == []
                continue
            assert s["case_type"] in CATALOGUE[action].case_types, s["id"]
            assert tiers and set(tiers) <= {"auto", "approval", "human_only"}, s["id"]
        for action in s["done"]:
            assert s["case_type"] in CATALOGUE[action].case_types, s["id"]
        assert s["why"], s["id"]


def test_the_decision_engine_alone_never_runs_a_persons_decision() -> None:
    report = evaluate("catalogue")
    summary = report.summary()
    assert summary["false_auto"] == 0
    assert summary["accuracy"] >= 0.95
    # The known gap: DISC-4 (no discount after a recent refusal) is policy text the Strategist must apply.
    assert [r.id for r in report.results if r.unexpected_auto] == ["A6"]


def test_judging() -> None:
    s: dict[str, Any] = {"acceptable": {"request_phone_confirmation": ["human_only"]}, "done": {}}
    assert person_required(s)
    r = judge(s, None, Result("X", "t", "x", s["acceptable"]))
    assert not r.correct and not r.false_auto  # waiting wasn't acceptable, but nothing ran alone
    waiting = {"acceptable": {NONE: [], "offer_discount": ["approval"]}, "done": {}}
    assert not person_required(waiting)
    assert judge(waiting, None, Result("Y", "t", "y", waiting["acceptable"])).correct


def test_llm_mode_uses_the_strategists_proposals_and_reports_cost() -> None:
    strategy = {
        "approach": "Remind first.",
        "proposals": [{"action": "send_confirmation_reminder", "params": {"channel": "sms"}, "rationale": "r"}],
    }
    llm = FakeLLM([[tool_use("submit_strategy", strategy)]])
    report = evaluate("llm", llm, only=["U1"], price_per_million=1.36)
    (r,) = report.results
    assert r.correct and r.proposed == ["send_confirmation_reminder"] and r.tier == str(Tier.AUTO)
    s = report.summary()
    assert s["tokens"] == 120 and s["cost_usd"] > 0
    assert "| U1 |" in markdown(report, "the starting estimates")
    failing = evaluate("llm", FakeLLM([[{"type": "text", "text": "no"}]] * 4), only=["U1"])
    assert failing.summary()["errors"] == 1
