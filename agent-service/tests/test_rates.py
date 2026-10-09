"""Measured rates and how the scorer uses them. Part of the decision engine: every branch is tested."""

from decimal import Decimal

import pytest

from revenueops.decision.rates import MIN_TRIALS, NO_ACTION, Rate, RateTable
from revenueops.decision.score import CaseFacts, score


def test_rate_and_its_interval() -> None:
    r = Rate(45, 100)
    assert r.p == 0.45
    lo, hi = r.interval()
    assert lo == pytest.approx(0.3561, abs=1e-3) and hi == pytest.approx(0.5475, abs=1e-3)
    assert Rate(0, 0).p == 0.0 and Rate(0, 0).interval() == (0.0, 1.0)
    lo0, hi0 = Rate(0, 50).interval()
    assert lo0 == 0.0 and 0 < hi0 < 0.1  # never a negative lower bound


def test_a_rate_is_used_only_with_enough_trials() -> None:
    table = RateTable(rates={("a", "x"): Rate(10, MIN_TRIALS), ("b", "x"): Rate(10, MIN_TRIALS - 1)})
    assert table.get("a", "x") == Rate(10, MIN_TRIALS)
    assert table.get("b", "x") is None
    assert table.get("c", "x") is None


def facts(rates: RateTable | None) -> CaseFacts:
    return CaseFacts("unconfirmed_order", Decimal(1000), 0.9, rates=rates)


def test_scorer_uses_measured_rates_as_a_pair() -> None:
    measured = RateTable(
        rates={
            ("send_confirmation_reminder", "unconfirmed_order"): Rate(36, 100),
            (NO_ACTION, "unconfirmed_order"): Rate(18, 100),
        }
    )
    s = score("send_confirmation_reminder", {"channel": "sms"}, facts(measured))
    assert (s.p_with, s.p_without, s.measured, s.trials) == (0.36, 0.18, True, 100)
    assert s.expected_value == Decimal("179.50")  # (0.36 - 0.18) x 1000 - 0.50

    assumed = score("send_confirmation_reminder", {"channel": "sms"}, facts(None))
    assert (assumed.p_with, assumed.p_without, assumed.measured, assumed.trials) == (0.45, 0.25, False, 0)


def test_half_measured_falls_back_to_the_estimates() -> None:
    only_with = RateTable(rates={("send_confirmation_reminder", "unconfirmed_order"): Rate(36, 100)})
    s = score("send_confirmation_reminder", {"channel": "sms"}, facts(only_with))
    assert (s.p_with, s.p_without, s.measured) == (0.45, 0.25, False)
    only_baseline = RateTable(rates={(NO_ACTION, "unconfirmed_order"): Rate(18, 100)})
    assert not score("send_confirmation_reminder", {"channel": "sms"}, facts(only_baseline)).measured
