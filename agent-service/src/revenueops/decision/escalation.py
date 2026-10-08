"""Escalation steps: which actions only make sense after a cheaper one has failed.

A phone call is worth more than a reminder on paper, but a reminder costs almost nothing and often
solves the problem alone. So for some case types an action waits until an earlier step has gone
unanswered for a while. Expected value decides between actions that are ready now; this decides
which ones are.
"""

from __future__ import annotations

from collections.abc import Mapping, Set
from dataclasses import dataclass


@dataclass(frozen=True)
class Step:
    after: str  # the action that must come first
    wait_hours: float  # how long it gets to work before escalating
    rule: str  # the policy rule that sets this order


STEPS: dict[tuple[str, str], Step] = {
    # (action, case type): what has to come first
    ("request_phone_confirmation", "unconfirmed_order"): Step("send_confirmation_reminder", 24, "COD-6"),
    # Refusal-risk orders are called straight away: COD-2 requires a call before dispatch.
    ("recommend_cancellation", "unconfirmed_order"): Step("request_phone_confirmation", 48, "COD-4"),
    ("recommend_cancellation", "refusal_risk"): Step("request_phone_confirmation", 48, "COD-4"),
    ("notify_customer_of_delay", "stalled_fulfilment"): Step("nudge_vendor", 48, "FUL-2"),
    ("offer_discount", "abandoned_cart"): Step("send_cart_reminder", 24, "DISC-5"),
}


@dataclass(frozen=True)
class Readiness:
    ready: bool
    waiting_for: str | None = None  # why not yet, for people and for the model
    rule: str | None = None


def step_for(action: str, case_type: str) -> Step | None:
    return STEPS.get((action, case_type))


def readiness(action: str, case_type: str, done_hours_ago: Mapping[str, float], available: Set[str]) -> Readiness:
    """Whether `action` may run now, given what was already done and how long ago (in hours)."""
    step = step_for(action, case_type)
    if step is None or step.after not in available:
        return Readiness(ready=True)  # no earlier step, or this store can't do it
    hours = done_hours_ago.get(step.after)
    if hours is None:
        return Readiness(False, f"Only after {step.after} has gone unanswered for {step.wait_hours:g}h", step.rule)
    if hours < step.wait_hours:
        left = step.wait_hours - hours
        return Readiness(False, f"{step.after} was done {hours:g}h ago; escalate in {left:g}h", step.rule)
    return Readiness(ready=True)
