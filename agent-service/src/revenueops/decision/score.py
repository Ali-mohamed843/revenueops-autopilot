"""Scores proposed actions: expected value, then the tier that decides who may carry them out.

    expected value = (P(recovered | action) - P(recovered | nothing)) x value at risk - expected cost

Tiers, from least to most oversight:
    auto        the service may run it on its own
    approval    a person must approve it first
    human_only  a person must decide and do it

The hard limits below are the policy rules that matter most, written in code so that no prompt,
policy edit or model output can loosen them.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from enum import IntEnum
from typing import Any

from revenueops.decision import priors
from revenueops.decision.catalogue import CATALOGUE, Kind
from revenueops.decision.escalation import readiness

CENTS = Decimal("0.01")

# Hard limits (EGP and percent). Each mirrors a rule in policies/ so citations line up.
REFUND_HUMAN_LIMIT = Decimal(300)  # RET-2
AUTO_DISCOUNT_MAX_PERCENT = 10  # DISC-1, DISC-2
AUTO_DISCOUNT_MAX_VALUE = Decimal(500)  # DISC-2
AUTO_DISCOUNT_MIN_CART = Decimal(300)  # DISC-1
GOODWILL_MAX_PERCENT = 5  # DISC-3
GOODWILL_MAX_VALUE = Decimal(250)  # DISC-3
LARGE_ORDER = Decimal(10_000)  # order changes and money on bigger orders get a human look
MIN_CONFIDENCE = 0.6  # below this, the facts behind the case are too uncertain to act alone


class Tier(IntEnum):
    AUTO = 0
    APPROVAL = 1
    HUMAN_ONLY = 2

    def __str__(self) -> str:
        return self.name.lower()


@dataclass(frozen=True)
class CaseFacts:
    case_type: str
    value_at_risk: Decimal
    confidence: float | None = None  # the investigation's confidence in its own findings
    done_hours_ago: Mapping[str, float] = field(default_factory=dict)  # actions already carried out
    available: frozenset[str] | None = None  # actions this store can do; None = the whole catalogue


@dataclass(frozen=True)
class Reason:
    tier: Tier
    text: str
    rule: str | None = None  # the policy rule it enforces


@dataclass(frozen=True)
class Score:
    action: str
    params: dict[str, Any]
    p_with: float
    p_without: float
    cost: Decimal
    expected_value: Decimal
    tier: Tier
    reasons: tuple[Reason, ...]
    ready: bool = True  # False while an earlier escalation step still has to work
    waiting_for: str | None = None


def score(action: str, params: dict[str, Any], case: CaseFacts) -> Score:
    """Score one action whose params were already checked against the catalogue."""
    spec = CATALOGUE[action]
    value = case.value_at_risk
    p_without = priors.BASELINE[case.case_type]
    p_with = priors.WITH_ACTION.get((action, case.case_type), p_without)

    cost = spec.cost
    if action == "offer_discount":
        # The discount is only paid when the customer comes back and uses it.
        cost += value * Decimal(params["percent"]) / 100 * Decimal(str(p_with))
    expected_value = (Decimal(str(p_with - p_without)) * value - cost).quantize(CENTS)

    reasons = _reasons(action, params, case)
    available = case.available if case.available is not None else frozenset(CATALOGUE)
    when = readiness(action, case.case_type, case.done_hours_ago, available)
    return Score(
        action=action,
        params=params,
        p_with=p_with,
        p_without=p_without,
        cost=cost.quantize(CENTS),
        expected_value=expected_value,
        tier=max(r.tier for r in reasons),
        reasons=reasons,
        ready=when.ready,
        waiting_for=when.waiting_for,
    )


def _reasons(action: str, params: dict[str, Any], case: CaseFacts) -> tuple[Reason, ...]:
    spec = CATALOGUE[action]
    value = case.value_at_risk
    reasons: list[Reason] = []

    def need(tier: Tier, text: str, rule: str | None = None) -> None:
        reasons.append(Reason(tier, text, rule))

    if spec.performed_by_human:
        rule = "COD-4" if action == "recommend_cancellation" else None
        need(Tier.HUMAN_ONLY, "A person has to carry this out", rule)

    if action == "process_return" and params["decision"] == "approve":
        refund = params.get("refund_amount", value)
        if refund > REFUND_HUMAN_LIMIT:
            need(Tier.HUMAN_ONLY, f"Refunds over {REFUND_HUMAN_LIMIT} EGP need a person", "RET-2")

    if action == "offer_discount":
        percent = params["percent"]
        discount = value * Decimal(percent) / 100
        if case.case_type == "late_shipment":
            if percent > GOODWILL_MAX_PERCENT or discount > GOODWILL_MAX_VALUE:
                need(
                    Tier.APPROVAL,
                    f"Goodwill discounts above {GOODWILL_MAX_PERCENT}% or {GOODWILL_MAX_VALUE} EGP need approval",
                    "DISC-3",
                )
        else:
            if percent > AUTO_DISCOUNT_MAX_PERCENT or discount > AUTO_DISCOUNT_MAX_VALUE:
                need(
                    Tier.APPROVAL,
                    f"Discounts above {AUTO_DISCOUNT_MAX_PERCENT}% or {AUTO_DISCOUNT_MAX_VALUE} EGP need approval",
                    "DISC-2",
                )
            if value < AUTO_DISCOUNT_MIN_CART:
                need(Tier.APPROVAL, f"Carts under {AUTO_DISCOUNT_MIN_CART} EGP get no automatic discount", "DISC-1")

    if action == "request_deposit":
        need(Tier.APPROVAL, "Asking for a deposit needs approval", "COD-3")

    if not spec.reversible and not spec.low_harm:
        need(Tier.APPROVAL, "It can't be undone")

    if spec.kind in (Kind.ORDER_CHANGE, Kind.MONEY) and value > LARGE_ORDER:
        need(Tier.APPROVAL, f"Changes to orders over {LARGE_ORDER} EGP get a human look")

    if case.confidence is not None and case.confidence < MIN_CONFIDENCE:
        need(Tier.APPROVAL, f"The investigation's confidence ({case.confidence:.2f}) is below {MIN_CONFIDENCE}")

    if not reasons:
        why = "harmless if wrong" if spec.low_harm else "can be undone"
        need(Tier.AUTO, f"Within every limit, and {why}")
    return tuple(reasons)


def rank(scores: list[Score]) -> list[Score]:
    """Best first: actions ready now, then highest expected value, then the least oversight."""
    return sorted(scores, key=lambda s: (not s.ready, -s.expected_value, s.tier))


def recommended(ranked: list[Score]) -> Score | None:
    """The best action that can run now, if it is expected to gain anything at all."""
    best = ranked[0] if ranked else None
    return best if best is not None and best.ready and best.expected_value > 0 else None
