"""The simulated world: how customers actually respond. The scorer never sees these numbers.

They differ from the starting estimates in decision/priors.py on purpose (some actions work better
than assumed, some worse), and they depend on the customer's segment: a high-risk COD customer
ignores a reminder more often but answers a phone call; a large cart reacts more to a discount.
Measuring them from outcomes, instead of reading them, is the point of the exercise.

Everything here is simulated. It stands in for real customers until there are enough real outcomes.
"""

from __future__ import annotations

import random
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from revenueops.decision.rates import NO_ACTION

# Chance the revenue comes back with no action.
TRUE_BASELINE: dict[str, float] = {
    "unconfirmed_order": 0.18,
    "refusal_risk": 0.40,
    "stalled_fulfilment": 0.55,
    "late_shipment": 0.66,
    "abandoned_cart": 0.04,
    "stale_return": 0.45,
}

# Chance with the action, for an average customer.
TRUE_WITH: dict[tuple[str, str], float] = {
    ("send_confirmation_reminder", "unconfirmed_order"): 0.36,
    ("send_confirmation_reminder", "refusal_risk"): 0.47,
    ("request_phone_confirmation", "unconfirmed_order"): 0.58,
    ("request_phone_confirmation", "refusal_risk"): 0.71,
    ("hold_dispatch", "refusal_risk"): 0.46,
    ("hold_dispatch", "unconfirmed_order"): 0.22,
    ("request_deposit", "refusal_risk"): 0.66,
    ("recommend_cancellation", "unconfirmed_order"): 0.18,
    ("recommend_cancellation", "refusal_risk"): 0.40,
    ("nudge_vendor", "stalled_fulfilment"): 0.84,
    ("notify_customer_of_delay", "stalled_fulfilment"): 0.62,
    ("notify_customer_of_delay", "late_shipment"): 0.78,
    ("open_courier_ticket", "late_shipment"): 0.88,
    ("send_cart_reminder", "abandoned_cart"): 0.09,
    ("offer_discount", "abandoned_cart"): 0.21,
    ("offer_discount", "late_shipment"): 0.80,
    ("process_return", "stale_return"): 0.83,
}

MESSAGES = {"send_confirmation_reminder", "send_cart_reminder", "notify_customer_of_delay"}
CALLS = {"request_phone_confirmation", "request_deposit"}


def segment(case_type: str, value: Decimal, signals: Mapping[str, Any]) -> str:
    """Two dimensions that change how customers respond: COD risk and order size."""
    score = signals.get("risk_score")
    risk = (
        "high"
        if isinstance(score, int) and score >= 55
        else "medium"
        if isinstance(score, int) and score >= 30
        else "low"
    )
    size = "large" if value >= 10_000 else "mid" if value >= 2_000 else "small"
    return f"{risk}-risk · {size}"


def true_chance(case_type: str, action: str, seg: str) -> float:
    base = TRUE_BASELINE[case_type]
    if action == NO_ACTION:
        return base
    p = TRUE_WITH.get((action, case_type), base)
    uplift = p - base
    risk, size = seg.split(" · ")
    if action in MESSAGES:
        uplift *= {"high-risk": 0.5, "medium-risk": 1.0, "low-risk": 1.2}[risk]
    if action in CALLS:
        uplift *= {"high-risk": 1.25, "medium-risk": 1.0, "low-risk": 0.9}[risk]
    if action == "offer_discount":
        uplift *= {"large": 1.3, "mid": 1.0, "small": 0.8}[size]
    return min(max(base + uplift, 0.01), 0.99)


def draw(rng: random.Random, case_type: str, action: str, seg: str) -> bool:
    return rng.random() < true_chance(case_type, action, seg)
