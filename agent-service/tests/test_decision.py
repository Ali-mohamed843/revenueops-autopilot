"""The decision engine: catalogue parameters, expected value and tiers. Pure, so every branch is tested."""

import re
from decimal import Decimal
from pathlib import Path

import pytest

from revenueops.decision import priors
from revenueops.decision.catalogue import CATALOGUE, actions_for
from revenueops.decision.escalation import STEPS, readiness
from revenueops.decision.score import CaseFacts, Score, Tier, rank, recommended, score
from revenueops.policies import all_policies

D = Decimal
ALL = {
    "order_confirmations",
    "delivery_tracking",
    "cod_risk_scores",
    "abandoned_carts",
    "returns",
    "dispatch_hold",
    "discount_codes",
    "return_decisions",
}


def facts(case_type: str, value: str = "1000", confidence: float | None = 0.9) -> CaseFacts:
    return CaseFacts(case_type, D(value), confidence)


def tiered(action: str, params: dict[str, object], case: CaseFacts) -> tuple[Tier, list[str | None]]:
    s = score(action, CATALOGUE[action].check_params(params, case.value_at_risk), case)
    return s.tier, [r.rule for r in s.reasons]


# --------------------------------------------------------------- catalogue


def test_every_action_has_priors_for_every_case_type_it_serves() -> None:
    for spec in CATALOGUE.values():
        for case_type in spec.case_types:
            assert case_type in priors.BASELINE
            assert (spec.key, case_type) in priors.WITH_ACTION, (spec.key, case_type)


def test_actions_for_filters_by_case_type_and_capabilities() -> None:
    keys = {a.key for a in actions_for("unconfirmed_order", ALL)}
    assert keys == {
        "send_confirmation_reminder",
        "request_phone_confirmation",
        "hold_dispatch",
        "recommend_cancellation",
    }
    assert "send_confirmation_reminder" not in {a.key for a in actions_for("unconfirmed_order", set())}


def test_params_are_checked_and_normalised() -> None:
    discount = CATALOGUE["offer_discount"]
    assert discount.check_params({"percent": 10.0, "valid_hours": "48"}, D(1000)) == {"percent": 10, "valid_hours": 48}
    refund = CATALOGUE["process_return"]
    assert refund.check_params({"decision": "approve", "refund_amount": 99.999}, D(500)) == {
        "decision": "approve",
        "refund_amount": D("100.00"),
    }
    assert refund.check_params({"decision": "reject", "refund_amount": None}, D(500)) == {"decision": "reject"}


@pytest.mark.parametrize(
    ("action", "params", "message"),
    [
        ("send_cart_reminder", {"channel": "pigeon"}, "channel must be one of whatsapp, sms"),
        ("send_cart_reminder", {}, "needs the parameter channel"),
        ("send_cart_reminder", {"channel": "sms", "tone": "warm"}, "takes no parameter tone"),
        ("offer_discount", {"percent": True, "valid_hours": 48}, "percent must be a number"),
        ("offer_discount", {"percent": [10], "valid_hours": 48}, "percent must be a number"),
        ("offer_discount", {"percent": "ten", "valid_hours": 48}, "percent must be a number"),
        ("offer_discount", {"percent": 7.5, "valid_hours": 48}, "percent must be a whole number"),
        ("offer_discount", {"percent": 0, "valid_hours": 48}, "percent must be at least 1"),
        ("offer_discount", {"percent": 25, "valid_hours": 48}, "percent must be at most 20"),
        ("process_return", {"decision": "approve", "refund_amount": 501}, "must not exceed the value at risk"),
    ],
)
def test_bad_params_are_rejected_with_a_message_for_the_model(
    action: str, params: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        CATALOGUE[action].check_params(params, D(500))


# ------------------------------------------------------------ expected value


def test_expected_value_is_uplift_times_value_minus_cost() -> None:
    s = score("send_confirmation_reminder", {"channel": "whatsapp"}, facts("unconfirmed_order"))
    # (0.45 - 0.25) x 1000 - 0.50
    assert (s.p_with, s.p_without, s.cost, s.expected_value) == (0.45, 0.25, D("0.50"), D("199.50"))


def test_a_discount_costs_only_when_it_is_used() -> None:
    s = score("offer_discount", {"percent": 10, "valid_hours": 48}, facts("abandoned_cart"))
    # cost: 10% of 1000, paid with probability 0.18 = 18; EV: (0.18 - 0.05) x 1000 - 18
    assert (s.cost, s.expected_value) == (D("18.00"), D("112.00"))


def test_an_action_without_a_prior_for_the_case_gains_nothing() -> None:
    s = score("nudge_vendor", {}, facts("late_shipment"))
    assert s.p_with == s.p_without and s.expected_value == 0


# --------------------------------------------------------------------- tiers


def test_low_harm_messages_and_reversible_changes_run_automatically() -> None:
    assert tiered("send_confirmation_reminder", {"channel": "sms"}, facts("unconfirmed_order")) == (Tier.AUTO, [None])
    assert tiered("hold_dispatch", {}, facts("refusal_risk")) == (Tier.AUTO, [None])
    assert tiered("open_courier_ticket", {}, facts("late_shipment"))[0] == Tier.AUTO


def test_messages_stay_automatic_even_on_large_orders() -> None:
    assert tiered("send_confirmation_reminder", {"channel": "sms"}, facts("unconfirmed_order", "50000"))[0] == Tier.AUTO


def test_order_changes_on_large_orders_need_approval() -> None:
    assert tiered("hold_dispatch", {}, facts("refusal_risk", "10000.01"))[0] == Tier.APPROVAL
    assert tiered("hold_dispatch", {}, facts("refusal_risk", "10000"))[0] == Tier.AUTO


def test_human_tasks_are_human_only() -> None:
    assert tiered("request_phone_confirmation", {}, facts("unconfirmed_order"))[0] == Tier.HUMAN_ONLY
    tier, rules = tiered("recommend_cancellation", {}, facts("unconfirmed_order"))
    assert tier == Tier.HUMAN_ONLY and "COD-4" in rules


def test_deposits_always_need_approval() -> None:
    assert tiered("request_deposit", {"percent": 10}, facts("refusal_risk")) == (Tier.APPROVAL, ["COD-3"])


@pytest.mark.parametrize(
    ("value", "percent", "tier", "rule"),
    [
        ("1000", 10, Tier.AUTO, None),
        ("1000", 11, Tier.APPROVAL, "DISC-2"),  # over 10%
        ("6000", 10, Tier.APPROVAL, "DISC-2"),  # worth over 500 EGP
        ("299", 5, Tier.APPROVAL, "DISC-1"),  # cart under 300 EGP
    ],
)
def test_cart_discount_limits(value: str, percent: int, tier: Tier, rule: str | None) -> None:
    got, rules = tiered("offer_discount", {"percent": percent, "valid_hours": 48}, facts("abandoned_cart", value))
    assert got == tier and rule in rules


@pytest.mark.parametrize(
    ("value", "percent", "tier"),
    [("1000", 5, Tier.AUTO), ("1000", 6, Tier.APPROVAL), ("6000", 5, Tier.APPROVAL)],
)
def test_goodwill_discount_limits(value: str, percent: int, tier: Tier) -> None:
    got, _ = tiered("offer_discount", {"percent": percent, "valid_hours": 48}, facts("late_shipment", value))
    assert got == tier


@pytest.mark.parametrize(
    ("params", "value", "tier"),
    [
        ({"decision": "approve", "refund_amount": 300}, "400", Tier.APPROVAL),
        ({"decision": "approve", "refund_amount": "300.01"}, "400", Tier.HUMAN_ONLY),
        ({"decision": "approve"}, "301", Tier.HUMAN_ONLY),  # refunds the full value by default
        ({"decision": "reject"}, "5000", Tier.APPROVAL),
    ],
)
def test_refunds_over_300_need_a_person(params: dict[str, object], value: str, tier: Tier) -> None:
    got, rules = tiered("process_return", params, facts("stale_return", value))
    assert got == tier
    assert ("RET-2" in rules) == (tier == Tier.HUMAN_ONLY)


def test_uncertain_investigations_need_approval_even_for_harmless_actions() -> None:
    assert tiered("send_confirmation_reminder", {"channel": "sms"}, facts("unconfirmed_order", confidence=0.59))[0] == (
        Tier.APPROVAL
    )
    assert tiered("send_confirmation_reminder", {"channel": "sms"}, facts("unconfirmed_order", confidence=None))[0] == (
        Tier.AUTO
    )


def test_tier_names() -> None:
    assert [str(t) for t in Tier] == ["auto", "approval", "human_only"]


# ------------------------------------------------------------------- ranking


def test_ranks_ready_actions_first_then_expected_value_then_least_oversight() -> None:
    case = facts("refusal_risk")  # here the call is not an escalation step (COD-2)
    phone = score("request_phone_confirmation", {}, case)  # EV 192, human_only
    reminder = score("send_confirmation_reminder", {"channel": "sms"}, case)  # EV 99.50, auto
    later = score("recommend_cancellation", {}, case)  # after a failed call
    ranked = rank([later, reminder, phone])
    assert [s.action for s in ranked] == [
        "request_phone_confirmation",
        "send_confirmation_reminder",
        "recommend_cancellation",
    ]
    assert recommended(ranked) is phone

    same_value = Score("b", {}, 0.5, 0.4, D(0), D(10), Tier.APPROVAL, ())
    assert rank([same_value, Score("a", {}, 0.5, 0.4, D(0), D(10), Tier.AUTO, ())])[0].action == "a"


def test_a_cheap_reminder_comes_before_a_call_for_unconfirmed_orders() -> None:
    case = facts("unconfirmed_order")
    phone = score("request_phone_confirmation", {}, case)
    reminder = score("send_confirmation_reminder", {"channel": "sms"}, case)
    assert phone.expected_value > reminder.expected_value  # the call is worth more on paper...
    ranked = rank([phone, reminder])
    assert [s.action for s in ranked] == ["send_confirmation_reminder", "request_phone_confirmation"]
    assert recommended(ranked) is reminder  # ...but the reminder goes first
    assert not phone.ready
    assert phone.waiting_for == "Only after send_confirmation_reminder has gone unanswered for 24h"


def test_nothing_is_recommended_while_every_action_waits() -> None:
    assert recommended(rank([score("request_phone_confirmation", {}, facts("unconfirmed_order"))])) is None


def test_nothing_is_recommended_without_a_gain() -> None:
    assert recommended([score("recommend_cancellation", {}, facts("unconfirmed_order"))]) is None
    assert recommended([]) is None


# --------------------------------------------- code limits match the policies


def test_every_rule_the_scorer_cites_exists_in_the_policies() -> None:
    decision = Path(__file__).parent.parent / "src" / "revenueops" / "decision"
    source = "".join((decision / f).read_text(encoding="utf-8") for f in ("score.py", "escalation.py"))
    cited = set(re.findall(r"\b[A-Z]{2,4}-\d+\b", source))
    known = {rule for p in all_policies() for rule in p.rules}
    assert cited and cited <= known, cited - known


# ---------------------------------------------------------------- escalation

AVAILABLE = frozenset(CATALOGUE)


@pytest.mark.parametrize(
    ("done", "available", "ready", "waiting"),
    [
        ({}, AVAILABLE, False, "Only after send_confirmation_reminder has gone unanswered for 24h"),
        (
            {"send_confirmation_reminder": 6},
            AVAILABLE,
            False,
            "send_confirmation_reminder was done 6h ago; escalate in 18h",
        ),
        ({"send_confirmation_reminder": 24}, AVAILABLE, True, None),
        ({}, AVAILABLE - {"send_confirmation_reminder"}, True, None),  # the store can't send reminders
    ],
)
def test_escalation_readiness(
    done: dict[str, float], available: frozenset[str], ready: bool, waiting: str | None
) -> None:
    r = readiness("request_phone_confirmation", "unconfirmed_order", done, available)
    assert (r.ready, r.waiting_for) == (ready, waiting)
    assert r.rule == (None if ready else "COD-6")


def test_actions_without_a_step_are_always_ready() -> None:
    assert readiness("send_confirmation_reminder", "unconfirmed_order", {}, AVAILABLE).ready
    assert readiness("request_phone_confirmation", "refusal_risk", {}, AVAILABLE).ready  # COD-2: call at once


def test_score_uses_what_was_done_and_what_the_store_can_do() -> None:
    done = CaseFacts("unconfirmed_order", D(1000), 0.9, done_hours_ago={"send_confirmation_reminder": 30})
    assert score("request_phone_confirmation", {}, done).ready
    no_reminders = CaseFacts("unconfirmed_order", D(1000), 0.9, available=frozenset({"request_phone_confirmation"}))
    assert score("request_phone_confirmation", {}, no_reminders).ready


def test_every_escalation_step_is_a_real_action_for_its_case_type() -> None:
    for (action, case_type), step in STEPS.items():
        assert case_type in CATALOGUE[action].case_types
        assert case_type in CATALOGUE[step.after].case_types
        assert step.wait_hours > 0
