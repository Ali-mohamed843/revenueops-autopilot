"""Generic e-commerce models.

Adapters translate a store's own API into these. Fields a store can't provide are optional, and
`Capabilities` says which whole features a store has, so detectors that need a missing feature
switch themselves off instead of misreading empty data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class OrderStatus(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    PROCESSING = "processing"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"


class ShipmentStatus(StrEnum):
    NOT_DISPATCHED = "not_dispatched"
    IN_TRANSIT = "in_transit"
    OUT_FOR_DELIVERY = "out_for_delivery"
    FAILED_ATTEMPT = "failed_attempt"
    DELIVERED = "delivered"
    RETURNED = "returned"
    LOST = "lost"


class ConfirmationStatus(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    DECLINED = "declined"
    NO_RESPONSE = "no_response"
    EXPIRED = "expired"


class ReturnStatus(StrEnum):
    REQUESTED = "requested"
    APPROVED = "approved"
    REJECTED = "rejected"
    RECEIVED = "received"
    REFUNDED = "refunded"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class Capabilities:
    """Which features a store exposes. Detectors declare what they need from this list."""

    order_confirmations: bool = False  # pre-dispatch "confirm your order" messages
    delivery_tracking: bool = False  # shipments, couriers, delivery attempts
    cod_risk_scores: bool = False  # per-customer cash-on-delivery risk
    abandoned_carts: bool = False
    returns: bool = False
    payment_failures: bool = False  # online payments that can fail and be retried

    def has(self, *features: str) -> bool:
        return all(getattr(self, f) for f in features)


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True)


class Customer(_Model):
    key: str | None  # stable identity across orders; for COD stores, the normalized phone
    user_id: str | None = None
    registered: bool = False
    name: str | None = None
    email: str | None = None
    phone: str | None = None


class Shipment(_Model):
    id: str
    status: ShipmentStatus
    courier: str | None = None
    courier_promised_days: int | None = None
    tracking_number: str | None = None
    cash_on_delivery: bool = True
    cod_amount: Decimal = Decimal(0)
    cod_collected: bool = False
    attempt_count: int = 0
    risk_score: int | None = None  # store's own COD risk score (0-100) at shipment creation
    return_reason: str | None = None
    dispatched_at: datetime | None = None
    delivered_at: datetime | None = None
    returned_at: datetime | None = None


class Confirmation(_Model):
    channel: str
    status: ConfirmationStatus
    expired: bool = False  # still pending, but past its deadline: never answered
    sent_at: datetime | None = None
    responded_at: datetime | None = None
    expires_at: datetime | None = None
    decline_reason: str | None = None
    reminders_sent: int = 0


class Order(_Model):
    id: str
    status: OrderStatus
    total: Decimal
    currency: str
    customer: Customer
    vendor_id: str | None = None
    vendor_name: str | None = None
    governorate: str | None = None
    city: str | None = None
    payment_method: str | None = None
    payment_status: str | None = None
    shipment: Shipment | None = None
    confirmation: Confirmation | None = None  # the latest one
    created_at: datetime
    updated_at: datetime


class OrderLine(_Model):
    product_name: str
    sku: str | None = None
    quantity: int
    unit_price: Decimal
    total_price: Decimal


class StatusChange(_Model):
    from_status: OrderStatus
    to_status: OrderStatus
    reason: str | None = None
    at: datetime


class Note(_Model):
    content: str
    author: str | None = None
    at: datetime


class DeliveryAttempt(_Model):
    number: int
    outcome: str
    notes: str | None = None
    rescheduled_for: datetime | None = None
    at: datetime


class ReturnRequest(_Model):
    id: str
    order_id: str
    product_name: str | None = None
    quantity: int
    reason: str
    comment: str | None = None
    status: ReturnStatus
    item_value: Decimal | None = None
    refund_amount: Decimal | None = None
    created_at: datetime
    updated_at: datetime


class CustomerProfile(_Model):
    """A customer's track record with the store."""

    key: str
    user_id: str | None = None
    name: str | None = None
    email: str | None = None
    risk_score: int | None = None  # 0 = reliable, 100 = certain to fail
    cod_blocked: bool = False
    phone_verified: bool = False
    delivered_count: int = 0
    refused_count: int = 0
    returned_count: int = 0
    no_answer_count: int = 0
    delivered_value: Decimal = Decimal(0)
    orders_by_status: dict[str, int] = {}
    first_order_at: datetime | None = None
    last_order_at: datetime | None = None


class OrderDetail(Order):
    subtotal: Decimal | None = None
    shipping_cost: Decimal | None = None
    discount: Decimal | None = None
    customer_note: str | None = None
    lines: list[OrderLine] = []
    history: list[StatusChange] = []  # oldest first
    notes: list[Note] = []  # oldest first
    attempts: list[DeliveryAttempt] = []
    confirmations: list[Confirmation] = []  # oldest first
    returns: list[ReturnRequest] = []


class LateShipment(_Model):
    order_id: str
    order_status: OrderStatus
    shipment: Shipment
    governorate: str | None = None
    days_since_dispatch: float


class CartLine(_Model):
    product_name: str | None = None
    sku: str | None = None
    quantity: int
    price_when_added: Decimal
    price_now: Decimal | None = None  # None when the product no longer exists
    stock: int | None = None


class Cart(_Model):
    id: str
    customer: Customer
    value: Decimal
    currency: str = "EGP"
    lines: list[CartLine] = []
    last_activity_at: datetime
