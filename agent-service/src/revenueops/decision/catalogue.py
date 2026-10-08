"""Every action the agents may propose. The Strategist chooses from this list; it cannot invent actions."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any


class Kind(StrEnum):
    MESSAGE = "message"  # a message to the customer or vendor
    ORDER_CHANGE = "order_change"  # changes an order's state
    MONEY = "money"  # gives away or moves money: discounts, deposits, refunds
    TASK = "task"  # work a person does, e.g. a phone call


@dataclass(frozen=True)
class Param:
    name: str
    kind: str  # "int", "money" or "choice"
    description: str
    min: int | None = None
    max: int | None = None
    choices: tuple[str, ...] = ()
    required: bool = True
    at_most_value_at_risk: bool = False  # e.g. a refund can't exceed what was paid

    def check(self, value: Any, value_at_risk: Decimal) -> int | Decimal | str:
        if self.kind == "choice":
            if value not in self.choices:
                raise ValueError(f"{self.name} must be one of {', '.join(self.choices)}")
            return str(value)
        if isinstance(value, bool) or not isinstance(value, int | float | str):
            raise ValueError(f"{self.name} must be a number")
        try:
            number = Decimal(str(value))
        except ArithmeticError as e:
            raise ValueError(f"{self.name} must be a number") from e
        if self.kind == "int" and number != number.to_integral_value():
            raise ValueError(f"{self.name} must be a whole number")
        if self.min is not None and number < self.min:
            raise ValueError(f"{self.name} must be at least {self.min}")
        if self.max is not None and number > self.max:
            raise ValueError(f"{self.name} must be at most {self.max}")
        if self.at_most_value_at_risk and number > value_at_risk:
            raise ValueError(f"{self.name} must not exceed the value at risk ({value_at_risk})")
        return int(number) if self.kind == "int" else number.quantize(Decimal("0.01"))


@dataclass(frozen=True)
class ActionSpec:
    key: str
    title: str
    description: str
    kind: Kind
    case_types: tuple[str, ...]
    reversible: bool  # can the executor undo it completely?
    low_harm: bool = False  # irreversible, but harmless if wrong (a polite reminder)
    performed_by_human: bool = False
    cost: Decimal = Decimal(0)  # direct cost in EGP: message fees, staff time
    needs: tuple[str, ...] = ()  # store Capabilities flags
    params: tuple[Param, ...] = field(default=())

    def check_params(self, params: dict[str, Any], value_at_risk: Decimal) -> dict[str, Any]:
        """Validated, normalised parameters. Raises ValueError with a message meant for the model."""
        known = {p.name for p in self.params}
        unknown = sorted(set(params) - known)
        if unknown:
            raise ValueError(f"{self.key} takes no parameter {', '.join(unknown)}")
        checked: dict[str, Any] = {}
        for p in self.params:
            if p.name in params and params[p.name] is not None:
                checked[p.name] = p.check(params[p.name], value_at_risk)
            elif p.required:
                raise ValueError(f"{self.key} needs the parameter {p.name}")
        return checked


CHANNEL = Param("channel", "choice", "How to reach the customer", choices=("whatsapp", "sms"))

_ACTIONS = (
    ActionSpec(
        key="send_confirmation_reminder",
        title="Send a confirmation reminder",
        description="Remind the customer to confirm their COD order by message.",
        kind=Kind.MESSAGE,
        case_types=("unconfirmed_order", "refusal_risk"),
        reversible=False,
        low_harm=True,
        cost=Decimal("0.50"),
        needs=("order_confirmations",),
        params=(CHANNEL,),
    ),
    ActionSpec(
        key="request_phone_confirmation",
        title="Confirm by phone",
        description="A support agent calls the customer to confirm the order before dispatch.",
        kind=Kind.TASK,
        case_types=("unconfirmed_order", "refusal_risk"),
        reversible=False,
        performed_by_human=True,
        cost=Decimal(8),
    ),
    ActionSpec(
        key="hold_dispatch",
        title="Hold dispatch",
        description="Keep the order from shipping until the customer confirms.",
        kind=Kind.ORDER_CHANGE,
        case_types=("refusal_risk", "unconfirmed_order"),
        reversible=True,
        needs=("dispatch_hold",),
    ),
    ActionSpec(
        key="request_deposit",
        title="Ask for a deposit",
        description="Ask the customer to prepay part of a risky COD order before it ships.",
        kind=Kind.MONEY,
        case_types=("refusal_risk",),
        reversible=True,
        params=(Param("percent", "int", "Share of the order total to prepay", min=5, max=20),),
    ),
    ActionSpec(
        key="recommend_cancellation",
        title="Recommend cancelling",
        description="Recommend that staff cancel the order and release its stock.",
        kind=Kind.ORDER_CHANGE,
        case_types=("unconfirmed_order", "refusal_risk"),
        reversible=False,
        performed_by_human=True,
    ),
    ActionSpec(
        key="nudge_vendor",
        title="Nudge the vendor",
        description="Message the vendor that a confirmed order is waiting to ship.",
        kind=Kind.MESSAGE,
        case_types=("stalled_fulfilment",),
        reversible=False,
        low_harm=True,
    ),
    ActionSpec(
        key="notify_customer_of_delay",
        title="Tell the customer about the delay",
        description="Apologise for the delay and give a new expected delivery date.",
        kind=Kind.MESSAGE,
        case_types=("stalled_fulfilment", "late_shipment"),
        reversible=False,
        low_harm=True,
        cost=Decimal("0.50"),
        params=(CHANNEL, Param("new_eta_days", "int", "Days until the new expected delivery", min=1, max=14)),
    ),
    ActionSpec(
        key="open_courier_ticket",
        title="Open a courier ticket",
        description="Ask the courier to trace a shipment that is past its promised delivery.",
        kind=Kind.TASK,
        case_types=("late_shipment",),
        reversible=True,
        needs=("delivery_tracking",),
    ),
    ActionSpec(
        key="send_cart_reminder",
        title="Send a cart reminder",
        description="Remind the customer of the items left in their cart.",
        kind=Kind.MESSAGE,
        case_types=("abandoned_cart",),
        reversible=False,
        low_harm=True,
        cost=Decimal("0.50"),
        needs=("abandoned_carts",),
        params=(CHANNEL,),
    ),
    ActionSpec(
        key="offer_discount",
        title="Offer a discount code",
        description="A single-use discount code, voidable until used.",
        kind=Kind.MONEY,
        case_types=("abandoned_cart", "late_shipment"),
        reversible=True,
        needs=("discount_codes",),
        params=(
            Param("percent", "int", "Discount percentage", min=1, max=20),
            Param("valid_hours", "int", "How long the code works", min=24, max=168),
        ),
    ),
    ActionSpec(
        key="process_return",
        title="Process the return",
        description="Approve or reject a waiting return request; approval refunds the customer.",
        kind=Kind.MONEY,
        case_types=("stale_return",),
        reversible=False,
        needs=("returns", "return_decisions"),
        params=(
            Param("decision", "choice", "approve or reject", choices=("approve", "reject")),
            Param(
                "refund_amount",
                "money",
                "EGP to refund when approving",
                min=0,
                required=False,
                at_most_value_at_risk=True,
            ),
        ),
    ),
)

CATALOGUE: dict[str, ActionSpec] = {a.key: a for a in _ACTIONS}


def actions_for(case_type: str, has: set[str]) -> list[ActionSpec]:
    """The actions that fit a case type, on a store with the capabilities in `has`."""
    return [a for a in _ACTIONS if case_type in a.case_types and set(a.needs) <= has]
