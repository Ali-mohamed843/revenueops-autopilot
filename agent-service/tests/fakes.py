"""Test doubles: an in-memory store and a scripted model."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from revenueops.adapters.base import Conflict, NotFound
from revenueops.agents.llm import LLMResponse, Usage
from revenueops.commerce.models import (
    Capabilities,
    Cart,
    CartLine,
    Customer,
    CustomerProfile,
    Discount,
    DispatchHold,
    LateShipment,
    Order,
    OrderDetail,
    OrderStatus,
    ReturnRequest,
    ReturnStatus,
    Shipment,
    ShipmentStatus,
)

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)


def ago(hours: float) -> datetime:
    return NOW - timedelta(hours=hours)


def order(
    id: str,
    status: OrderStatus = OrderStatus.PENDING,
    *,
    hours_since_update: float = 1,
    total: str = "1000",
    risk: int | None = 20,
    customer_key: str = "01099000001",
) -> Order:
    return Order(
        id=id,
        status=status,
        total=Decimal(total),
        currency="EGP",
        customer=Customer(key=customer_key, registered=True, name="Test Buyer", phone=customer_key),
        governorate="Cairo",
        payment_method="cash_on_delivery",
        shipment=Shipment(id=f"s-{id}", status=ShipmentStatus.NOT_DISPATCHED, risk_score=risk),
        created_at=ago(hours_since_update + 1),
        updated_at=ago(hours_since_update),
    )


@dataclass
class FakeStore:
    """An in-memory StoreAdapter. Filters like the real API does."""

    orders: list[Order] = field(default_factory=list)
    carts: list[Cart] = field(default_factory=list)
    late: list[LateShipment] = field(default_factory=list)
    returns: list[ReturnRequest] = field(default_factory=list)
    profiles: dict[str, CustomerProfile] = field(default_factory=dict)
    capabilities: Capabilities = field(
        default_factory=lambda: Capabilities(
            order_confirmations=True,
            delivery_tracking=True,
            cod_risk_scores=True,
            abandoned_carts=True,
            returns=True,
            dispatch_hold=True,
            discount_codes=True,
            return_decisions=True,
        )
    )
    name: str = "fake"
    notes: list[tuple[str, str, str]] = field(default_factory=list)
    discounts: dict[str, Discount] = field(default_factory=dict)
    now: datetime = NOW

    def list_orders(self, statuses: Sequence[OrderStatus], *, unchanged_for_hours: float | None = None) -> list[Order]:
        cutoff = self.now - timedelta(hours=unchanged_for_hours or 0)
        return [
            o for o in self.orders if o.status in statuses and (unchanged_for_hours is None or o.updated_at <= cutoff)
        ]

    def get_order(self, order_id: str) -> OrderDetail:
        for o in self.orders:
            if o.id == order_id:
                return OrderDetail(**o.model_dump())
        raise NotFound(order_id)

    def get_customer(self, customer_key: str) -> CustomerProfile | None:
        return self.profiles.get(customer_key)

    def list_idle_carts(self, idle_hours: float) -> list[Cart]:
        return [c for c in self.carts if c.last_activity_at <= self.now - timedelta(hours=idle_hours)]

    def list_late_shipments(self) -> list[LateShipment]:
        return list(self.late)

    def list_open_returns(self, older_than_hours: float) -> list[ReturnRequest]:
        cutoff = self.now - timedelta(hours=older_than_hours)
        return [r for r in self.returns if r.status == ReturnStatus.REQUESTED and r.created_at <= cutoff]

    def add_order_note(self, order_id: str, text: str, author: str) -> None:
        self.notes.append((order_id, text, author))

    # ------------------------------------------------------------- actions

    def _order(self, order_id: str) -> Order:
        for o in self.orders:
            if o.id == order_id:
                return o
        raise NotFound(order_id)

    def _replace_order(self, order: Order) -> None:
        self.orders = [order if o.id == order.id else o for o in self.orders]

    def _hold_state(self, order: Order) -> DispatchHold:
        s = order.shipment
        assert s is not None
        return DispatchHold(
            order_id=order.id,
            shipment_id=s.id,
            shipment_status=s.status,
            held=s.dispatch_hold,
            reason=s.dispatch_hold_reason,
            held_at=self.now if s.dispatch_hold else None,
        )

    def hold_dispatch(self, order_id: str, reason: str) -> DispatchHold:
        order = self._order(order_id)
        assert order.shipment is not None
        if order.shipment.status != ShipmentStatus.NOT_DISPATCHED:
            raise Conflict(f"Shipment is already {order.shipment.status}")
        if not order.shipment.dispatch_hold:
            shipment = order.shipment.model_copy(update={"dispatch_hold": True, "dispatch_hold_reason": reason})
            order = order.model_copy(update={"shipment": shipment})
            self._replace_order(order)
        return self._hold_state(order)

    def release_dispatch_hold(self, order_id: str) -> DispatchHold:
        order = self._order(order_id)
        assert order.shipment is not None
        shipment = order.shipment.model_copy(update={"dispatch_hold": False, "dispatch_hold_reason": None})
        order = order.model_copy(update={"shipment": shipment})
        self._replace_order(order)
        return self._hold_state(order)

    def create_discount(self, percent: int, valid_hours: int, min_order_value: Decimal | None) -> Discount:
        d = Discount(
            id=f"d{len(self.discounts) + 1}",
            code=f"RO-TEST{len(self.discounts) + 1:04d}",
            percent=percent,
            min_order_value=min_order_value,
            valid_until=self.now + timedelta(hours=valid_hours),
            active=True,
        )
        self.discounts[d.id] = d
        return d

    def void_discount(self, discount_id: str) -> Discount:
        d = self.discounts[discount_id]
        if d.uses:
            raise Conflict("The code was already used")
        self.discounts[discount_id] = d = d.model_copy(update={"active": False})
        return d

    def decide_return(self, return_id: str, decision: str, note: str) -> ReturnRequest:
        for r in self.returns:
            if r.id == return_id:
                status = ReturnStatus.APPROVED if decision == "approve" else ReturnStatus.REJECTED
                updated = r.model_copy(update={"status": status})
                self.returns = [updated if x.id == return_id else x for x in self.returns]
                return updated
        raise NotFound(return_id)


def cart(id: str, *, idle_hours: float, value: str = "500", email: str | None = "a@b.test") -> Cart:
    return Cart(
        id=id,
        customer=Customer(key="01099000002", user_id="u1", registered=True, email=email),
        value=Decimal(value),
        lines=[CartLine(product_name="Mug", quantity=2, price_when_added=Decimal(250))],
        last_activity_at=ago(idle_hours),
    )


# ------------------------------------------------------------------- the model


def tool_use(name: str, input: dict[str, Any], id: str | None = None) -> dict[str, Any]:
    return {"type": "tool_use", "id": id or f"tu_{name}", "name": name, "input": input}


def text(t: str) -> dict[str, Any]:
    return {"type": "text", "text": t}


@dataclass
class FakeLLM:
    """Replays scripted responses and records every request it receives."""

    script: list[list[dict[str, Any]]]
    model: str = "fake-model"
    stop_reason: str = "tool_use"
    requests: list[dict[str, Any]] = field(default_factory=list)

    def create(self, *, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> LLMResponse:
        self.requests.append({"system": system, "messages": [*messages], "tools": tools})
        if not self.script:
            raise AssertionError("FakeLLM ran out of scripted responses")
        content = self.script.pop(0)
        stop = self.stop_reason if any(b["type"] == "tool_use" for b in content) else "end_turn"
        return LLMResponse(content=content, stop_reason=stop, usage=Usage(100, 20))
