"""The contract every store adapter implements. The core imports this module, never an adapter."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from revenueops.commerce.models import (
    Capabilities,
    Cart,
    CustomerProfile,
    LateShipment,
    Order,
    OrderDetail,
    OrderStatus,
    ReturnRequest,
)


class StoreError(Exception):
    """The store could not answer (unreachable, rejected the request, returned something unexpected)."""


class NotFound(StoreError):
    """The store has no such record."""


class StoreAdapter(Protocol):
    name: str
    capabilities: Capabilities

    def list_orders(self, statuses: Sequence[OrderStatus], *, unchanged_for_hours: float | None = None) -> list[Order]:
        """Every order in one of `statuses`; optionally only those not updated for that long."""
        ...

    def get_order(self, order_id: str) -> OrderDetail: ...

    def get_customer(self, customer_key: str) -> CustomerProfile | None:
        """The customer's track record, or None when the store has never seen them."""
        ...

    def list_idle_carts(self, idle_hours: float) -> list[Cart]:
        """Carts that still hold items and the customer can be contacted about. Needs `abandoned_carts`."""
        ...

    def list_late_shipments(self) -> list[LateShipment]:
        """Shipments still on the road past the courier's promise. Needs `delivery_tracking`."""
        ...

    def list_open_returns(self, older_than_hours: float) -> list[ReturnRequest]:
        """Return requests nobody has acted on. Needs `returns`."""
        ...

    def add_order_note(self, order_id: str, text: str, author: str) -> None: ...
