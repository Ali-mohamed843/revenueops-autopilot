"""What each catalogue action does when it runs, and how to undo it.

A handler returns the before/after snapshots, its result, and any messages for the outbox. Who a
message goes to always comes from the store's own records (COMM-2), never from a model. Actions done
by a person (a phone call, a cancellation) have no handler body: the executor waits for a person to
report back.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from revenueops.adapters.base import StoreAdapter
from revenueops.agents.messenger import MessageBrief
from revenueops.cases.models import Case
from revenueops.commerce.models import OrderDetail

CUSTOMER_CHANNELS = ("whatsapp", "sms")
PHONE = re.compile(r"\+?\d{8,15}")  # after removing spaces, dashes and brackets


class ActionFailed(Exception):
    """The action could not be carried out (missing contact details, store refused, ...)."""


@dataclass(frozen=True)
class Message:
    channel: str
    recipient: str
    language: str
    body: str
    drafted_by: str


@dataclass
class Outcome:
    before: dict[str, Any]
    after: dict[str, Any]
    result: dict[str, Any] = field(default_factory=dict)
    messages: list[Message] = field(default_factory=list)


# Turns a brief into (language, body, drafted_by). The executor wires in the Messenger agent.
Writer = Callable[[MessageBrief], tuple[str, str, str]]


@dataclass
class Ctx:
    case: Case
    params: dict[str, Any]  # checked against the catalogue
    store: StoreAdapter
    write: Writer
    now: datetime
    _order: OrderDetail | None = None

    @property
    def order(self) -> OrderDetail:
        if self._order is None:
            self._order = self.store.get_order(self.case.subject_id)
        return self._order

    @property
    def order_ref(self) -> str:
        return "#" + self.case.subject_id[:8].upper()


@dataclass(frozen=True)
class Handler:
    run: Callable[[Ctx], Outcome] | None  # None: a person does it
    undo: Callable[[Ctx, dict[str, Any]], dict[str, Any]] | None = None  # undoes the store side
    changes_store: bool = False

    @property
    def by_human(self) -> bool:
        return self.run is None


# ------------------------------------------------------------------ helpers


def _money(value: Decimal) -> str:
    return f"{value:,.2f} EGP"


def _customer_message(ctx: Ctx, phone: str | None, brief: MessageBrief) -> Message:
    channel = str(ctx.params.get("channel", "whatsapp"))
    if channel in CUSTOMER_CHANNELS:
        if not phone:
            raise ActionFailed(f"No phone number to send a {channel} message to")
        phone = re.sub(r"[\s\-()]", "", phone)
        if not PHONE.fullmatch(phone):
            raise ActionFailed(f"{phone!r} is not a phone number a {channel} message can go to")
    language, body, drafted_by = ctx.write(brief)
    return Message(channel=channel, recipient=phone or "", language=language, body=body, drafted_by=drafted_by)


def _items(lines: Iterable[tuple[str, int]]) -> str:
    """'Mug x3, Lamp x1': one product can span several lines (sizes, colours), so merge them."""
    quantities: dict[str, int] = {}
    for name, qty in lines:
        quantities[name] = quantities.get(name, 0) + qty
    return ", ".join(f"{name} x{qty}" for name, qty in quantities.items())


def _order_facts(ctx: Ctx) -> dict[str, str]:
    o = ctx.order
    facts = {"order reference": ctx.order_ref, "order total": _money(o.total)}
    if o.customer.name:
        facts["customer name"] = o.customer.name
    if o.lines:
        facts["items"] = _items((line.product_name, line.quantity) for line in o.lines)
    return facts


def _order_state(o: OrderDetail) -> dict[str, Any]:
    state: dict[str, Any] = {"order_status": o.status}
    if o.shipment:
        state["shipment_status"] = o.shipment.status
        state["dispatch_hold"] = o.shipment.dispatch_hold
    if o.confirmation:
        state["confirmation"] = o.confirmation.status
    return state


def _message_only(ctx: Ctx, message: Message) -> Outcome:
    state = _order_state(ctx.order) if ctx.case.subject_type == "order" else {}
    return Outcome(before=state, after=state, messages=[message])


# ----------------------------------------------------------------- handlers


def confirmation_reminder(ctx: Ctx) -> Outcome:
    brief = MessageBrief(
        purpose="Remind the customer to confirm their cash-on-delivery order so it can be shipped; "
        "ask them to reply to confirm.",
        facts=_order_facts(ctx),
        must_include=(ctx.order_ref,),
        channel=str(ctx.params["channel"]),
    )
    return _message_only(ctx, _customer_message(ctx, ctx.order.customer.phone, brief))


def cart_reminder(ctx: Ctx) -> Outcome:
    signals = ctx.case.signals
    lines = signals.get("lines") or []
    facts = {"cart value": _money(ctx.case.value_at_risk)}
    if signals.get("customer_name"):
        facts["customer name"] = str(signals["customer_name"])
    if lines:
        facts["items"] = _items((str(line.get("product_name")), int(line.get("quantity") or 0)) for line in lines)
    brief = MessageBrief(
        purpose="Remind the customer that items are still waiting in their cart.",
        facts=facts,
        channel=str(ctx.params["channel"]),
    )
    phone = signals.get("customer_key")  # StoreForge keys carts by the customer's phone
    return _message_only(ctx, _customer_message(ctx, str(phone) if phone else None, brief))


def delay_notice(ctx: Ctx) -> Outcome:
    eta = (ctx.now + timedelta(days=int(ctx.params["new_eta_days"]))).strftime("%d/%m/%Y")
    brief = MessageBrief(
        purpose="Apologise that the order is late and give the new expected delivery date.",
        facts={**_order_facts(ctx), "new expected delivery date": eta},
        must_include=(ctx.order_ref, eta),
        channel=str(ctx.params["channel"]),
    )
    return _message_only(ctx, _customer_message(ctx, ctx.order.customer.phone, brief))


def deposit_request(ctx: Ctx) -> Outcome:
    amount = _money((ctx.order.total * Decimal(int(ctx.params["percent"])) / 100).quantize(Decimal("0.01")))
    brief = MessageBrief(
        purpose="Ask the customer to pay a deposit before the order ships; say our team will send the "
        "payment details. Do not include any payment link or account number.",
        facts={**_order_facts(ctx), "deposit": amount},
        must_include=(ctx.order_ref, amount),
    )
    return _message_only(ctx, _customer_message(ctx, ctx.order.customer.phone, brief))


def hold(ctx: Ctx) -> Outcome:
    before = _order_state(ctx.order)
    state = ctx.store.hold_dispatch(
        ctx.case.subject_id, f"RevenueOps: waiting for the buyer to confirm {ctx.order_ref}"
    )
    after = {**before, "dispatch_hold": state.held}
    return Outcome(before=before, after=after, result={"held": state.held, "reason": state.reason})


def release(ctx: Ctx, result: dict[str, Any]) -> dict[str, Any]:
    state = ctx.store.release_dispatch_hold(ctx.case.subject_id)
    return {"dispatch_hold": state.held}


def discount(ctx: Ctx) -> Outcome:
    code = ctx.store.create_discount(int(ctx.params["percent"]), int(ctx.params["valid_hours"]), None)
    until = code.valid_until.strftime("%d/%m/%Y %H:%M")
    if ctx.case.subject_type == "order":
        phone, facts = ctx.order.customer.phone, _order_facts(ctx)
        purpose = "Apologise for the delay and give the customer a discount code for their next order."
    else:
        phone, facts = ctx.case.signals.get("customer_key"), {"cart value": _money(ctx.case.value_at_risk)}
        purpose = "Offer the customer a discount code to complete the order waiting in their cart."
    brief = MessageBrief(
        purpose=purpose,
        facts={**facts, "discount code": code.code, "discount": f"{code.percent}%", "valid until": until},
        must_include=(code.code,),
    )
    message = _customer_message(ctx, str(phone) if phone else None, brief)
    return Outcome(
        before={},
        after={"discount": code.model_dump(mode="json")},
        result={"discount_id": code.id, "code": code.code},
        messages=[message],
    )


def void(ctx: Ctx, result: dict[str, Any]) -> dict[str, Any]:
    code = ctx.store.void_discount(str(result["discount_id"]))
    return {"discount_active": code.active}


def vendor_nudge(ctx: Ctx) -> Outcome:
    o = ctx.order
    hours = (ctx.now - o.updated_at).total_seconds() / 3600
    body = (
        f"Order {ctx.order_ref} has been {o.status} for {hours:.0f} hours and hasn't shipped yet. "
        "Please dispatch it today, or reply with what is blocking it."
    )
    message = Message("vendor", o.vendor_name or o.vendor_id or "vendor", "en", body, "template")
    return _message_only(ctx, message)


def courier_ticket(ctx: Ctx) -> Outcome:
    s = ctx.order.shipment
    if s is None or not s.courier:
        raise ActionFailed("The order has no courier to open a ticket with")
    days = ctx.case.signals.get("days_since_dispatch")
    body = (
        f"Please trace shipment {s.tracking_number or s.id} (order {ctx.order_ref}): dispatched {days} days ago, "
        f"promised in {s.courier_promised_days}, still not delivered. Reply with its location and a delivery date."
    )
    return _message_only(ctx, Message("courier", s.courier, "en", body, "template"))


def return_decision(ctx: Ctx) -> Outcome:
    decision = str(ctx.params["decision"])
    updated = ctx.store.decide_return(ctx.case.subject_id, decision, "RevenueOps decision")
    return Outcome(
        before={"return_status": "requested"},
        after={"return_status": updated.status},
        result={"decision": decision, "refund_amount": str(ctx.params.get("refund_amount", ""))},
    )


HANDLERS: dict[str, Handler] = {
    "send_confirmation_reminder": Handler(confirmation_reminder),
    "send_cart_reminder": Handler(cart_reminder),
    "notify_customer_of_delay": Handler(delay_notice),
    "request_deposit": Handler(deposit_request),
    "nudge_vendor": Handler(vendor_nudge),
    "open_courier_ticket": Handler(courier_ticket),
    "hold_dispatch": Handler(hold, undo=release, changes_store=True),
    "offer_discount": Handler(discount, undo=void, changes_store=True),
    "process_return": Handler(return_decision, changes_store=True),  # can't be undone
    "request_phone_confirmation": Handler(None),
    "recommend_cancellation": Handler(None),
}
