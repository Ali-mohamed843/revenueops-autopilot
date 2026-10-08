"""The shared contract every StoreAdapter must pass. Each adapter's tests call `check_adapter_contract`.

It checks what the core relies on, whatever the store: types, filters honoured, money as Decimal,
and that features the adapter claims it has actually return data of the right shape.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from revenueops.adapters.base import StoreAdapter
from revenueops.commerce.models import (
    Capabilities,
    Cart,
    LateShipment,
    Order,
    OrderDetail,
    OrderStatus,
    ReturnRequest,
    ReturnStatus,
)

UNSHIPPED = [OrderStatus.PENDING, OrderStatus.CONFIRMED, OrderStatus.PROCESSING]


def check_adapter_contract(store: StoreAdapter, now: datetime) -> None:
    assert isinstance(store.name, str) and store.name
    assert isinstance(store.capabilities, Capabilities)

    orders = store.list_orders(UNSHIPPED)
    assert orders, "the contract needs at least one unshipped order to check"
    for o in orders:
        assert isinstance(o, Order)
        assert o.status in UNSHIPPED, f"status filter not honoured: {o.status}"
        assert isinstance(o.total, Decimal) and o.total >= 0
        assert o.created_at.tzinfo is not None and o.updated_at.tzinfo is not None, "timestamps must be aware"
        assert o.updated_at <= now

    detail = store.get_order(orders[0].id)
    assert isinstance(detail, OrderDetail)
    assert detail.id == orders[0].id
    assert detail.history == sorted(detail.history, key=lambda h: h.at), "history must be oldest first"

    if detail.customer.key:
        profile = store.get_customer(detail.customer.key)
        assert profile is None or profile.key

    if store.capabilities.abandoned_carts:
        for c in store.list_idle_carts(24):
            assert isinstance(c, Cart)
            assert isinstance(c.value, Decimal)
            assert c.lines, "an idle cart must hold items"

    if store.capabilities.delivery_tracking:
        for late in store.list_late_shipments():
            assert isinstance(late, LateShipment)
            assert late.days_since_dispatch > 0

    if store.capabilities.returns:
        for r in store.list_open_returns(48):
            assert isinstance(r, ReturnRequest)
            assert r.status == ReturnStatus.REQUESTED
            assert (now - r.created_at).total_seconds() >= 48 * 3600 - 60
