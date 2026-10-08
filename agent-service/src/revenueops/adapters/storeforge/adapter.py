"""StoreForge (NestJS marketplace) through its integration API: /api/integration/*, x-api-key auth."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from decimal import Decimal
from typing import Any

import httpx

from revenueops.adapters.base import Conflict, NotFound, StoreError
from revenueops.commerce.models import (
    Capabilities,
    Cart,
    CartLine,
    Confirmation,
    ConfirmationStatus,
    Customer,
    CustomerProfile,
    DeliveryAttempt,
    Discount,
    DispatchHold,
    LateShipment,
    Note,
    Order,
    OrderDetail,
    OrderLine,
    OrderStatus,
    ReturnRequest,
    ReturnStatus,
    Shipment,
    ShipmentStatus,
    StatusChange,
)

PAGE_SIZE = 100
MAX_PAGES = 100  # 10,000 records: far beyond any one scan; stops a runaway loop

# StoreForge's shipment statuses, in generic terms
_SHIPMENT_STATUS = {
    "pending": ShipmentStatus.NOT_DISPATCHED,
    "dispatched": ShipmentStatus.IN_TRANSIT,
    "in_transit": ShipmentStatus.IN_TRANSIT,
    "out_for_delivery": ShipmentStatus.OUT_FOR_DELIVERY,
    "failed_attempt": ShipmentStatus.FAILED_ATTEMPT,
    "delivered": ShipmentStatus.DELIVERED,
    "returned": ShipmentStatus.RETURNED,
    "lost": ShipmentStatus.LOST,
}

Json = dict[str, Any]


def _money(value: Any) -> Decimal:
    return Decimal(str(value))


def _money_or_none(value: Any) -> Decimal | None:
    return None if value is None else Decimal(str(value))


class StoreForgeAdapter:
    name = "storeforge"
    capabilities = Capabilities(
        order_confirmations=True,
        delivery_tracking=True,
        cod_risk_scores=True,
        abandoned_carts=True,
        returns=True,
        payment_failures=False,  # cash on delivery only
        dispatch_hold=True,
        discount_codes=True,
        return_decisions=True,
    )

    def __init__(self, base_url: str, api_key: str, *, transport: httpx.BaseTransport | None = None) -> None:
        self._http = httpx.Client(
            base_url=base_url.rstrip("/") + "/integration",
            headers={"x-api-key": api_key},
            timeout=15,
            transport=transport,
        )

    def close(self) -> None:
        self._http.close()

    # ------------------------------------------------------------------ HTTP

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Json:
        return self._request("GET", path, params=params)

    def _request(self, method: str, path: str, **kwargs: Any) -> Json:
        try:
            response = self._http.request(method, path, **kwargs)
        except httpx.HTTPError as e:
            raise StoreError(f"StoreForge is unreachable: {e}") from e
        if response.status_code == 404:
            raise NotFound(f"StoreForge has no {path}")
        if response.status_code == 409:
            raise Conflict(f"StoreForge refused {method} {path}: {_message(response)}")
        if response.status_code >= 400:
            raise StoreError(f"StoreForge {method} {path} failed: HTTP {response.status_code} {response.text[:300]}")
        body: Json = response.json()
        return body

    def _pages(self, path: str, params: dict[str, Any]) -> Iterator[Json]:
        for page in range(1, MAX_PAGES + 1):
            body = self._get(path, {**params, "page": page, "limit": PAGE_SIZE})
            yield from body["items"]
            if page >= body["pages"]:
                return

    # --------------------------------------------------------------- reading

    def list_orders(self, statuses: Sequence[OrderStatus], *, unchanged_for_hours: float | None = None) -> list[Order]:
        params: dict[str, Any] = {"status": ",".join(statuses)}
        if unchanged_for_hours is not None:
            params["unchangedForHours"] = int(unchanged_for_hours)
        return [_order(o) for o in self._pages("/orders", params)]

    def get_order(self, order_id: str) -> OrderDetail:
        return _order_detail(self._get(f"/orders/{order_id}"))

    def get_customer(self, customer_key: str) -> CustomerProfile | None:
        try:
            body = self._get(f"/customers/{customer_key}/summary")
        except StoreError as e:
            if "HTTP 400" in str(e):  # not a phone number StoreForge can key on
                return None
            raise
        return _customer_profile(body)

    def list_idle_carts(self, idle_hours: float) -> list[Cart]:
        return [_cart(c) for c in self._pages("/carts", {"idleHours": int(idle_hours)})]

    def list_late_shipments(self) -> list[LateShipment]:
        return [
            LateShipment(
                order_id=s["orderId"],
                order_status=OrderStatus(s["orderStatus"]),
                shipment=_shipment(s),
                governorate=s.get("governorate"),
                days_since_dispatch=s["daysSinceDispatch"] or 0.0,
            )
            for s in self._pages("/shipments", {"overdue": "true"})
        ]

    def list_open_returns(self, older_than_hours: float) -> list[ReturnRequest]:
        params = {"status": "requested", "olderThanHours": int(older_than_hours)}
        return [_return(r) for r in self._pages("/returns", params)]

    # --------------------------------------------------------------- writing

    def add_order_note(self, order_id: str, text: str, author: str) -> None:
        self._request("POST", f"/orders/{order_id}/notes", json={"content": text, "author": author})

    def hold_dispatch(self, order_id: str, reason: str) -> DispatchHold:
        return _hold(self._request("POST", f"/orders/{order_id}/dispatch-hold", json={"reason": reason}))

    def release_dispatch_hold(self, order_id: str) -> DispatchHold:
        return _hold(self._request("DELETE", f"/orders/{order_id}/dispatch-hold"))

    def create_discount(self, percent: int, valid_hours: int, min_order_value: Decimal | None) -> Discount:
        body: dict[str, Any] = {"percent": percent, "validHours": valid_hours}
        if min_order_value is not None:
            body["minOrderValue"] = float(min_order_value)
        return _discount(self._request("POST", "/discounts", json=body))

    def void_discount(self, discount_id: str) -> Discount:
        return _discount(self._request("POST", f"/discounts/{discount_id}/void"))

    def decide_return(self, return_id: str, decision: str, note: str) -> ReturnRequest:
        return _return(
            self._request("POST", f"/returns/{return_id}/decision", json={"decision": decision, "note": note})
        )


# ------------------------------------------------------------------- mapping


def _customer(c: Json) -> Customer:
    return Customer(
        key=c.get("phoneKey"),
        user_id=c.get("userId"),
        registered=bool(c.get("registered")),
        name=c.get("name"),
        email=c.get("email"),
        phone=c.get("phone"),
    )


def _shipment(s: Json) -> Shipment:
    return Shipment(
        id=s["id"],
        status=_SHIPMENT_STATUS[s["status"]],
        courier=s.get("courierCode"),
        courier_promised_days=s.get("courierPromisedDays"),
        tracking_number=s.get("trackingNumber"),
        cash_on_delivery=s.get("isCod", True),
        cod_amount=_money(s.get("codAmount", 0)),
        cod_collected=bool(s.get("codCollected")),
        attempt_count=s.get("attemptCount", 0),
        risk_score=s.get("riskScoreAtDispatch"),
        dispatch_hold=bool(s.get("dispatchHold")),
        dispatch_hold_reason=s.get("dispatchHoldReason"),
        return_reason=s.get("rtoReason"),
        dispatched_at=s.get("dispatchedAt"),
        delivered_at=s.get("deliveredAt"),
        returned_at=s.get("returnedAt"),
    )


def _confirmation(c: Json) -> Confirmation:
    return Confirmation(
        channel=c["channel"],
        status=ConfirmationStatus(c["status"]),
        expired=bool(c.get("expired")),
        sent_at=c.get("sentAt"),
        responded_at=c.get("respondedAt"),
        expires_at=c.get("expiresAt"),
        decline_reason=c.get("declineReason"),
        reminders_sent=c.get("remindersSent", 0),
    )


def _order_fields(o: Json) -> dict[str, Any]:
    return {
        "id": o["id"],
        "status": OrderStatus(o["status"]),
        "total": _money(o["total"]),
        "currency": o["currency"],
        "customer": _customer(o["customer"]),
        "vendor_id": o.get("vendorId"),
        "vendor_name": o.get("vendorName"),
        "governorate": o.get("shippingGovernorate"),
        "city": o.get("shippingCity"),
        "payment_method": o.get("paymentMethod"),
        "payment_status": o.get("paymentStatus"),
        "shipment": _shipment(o["shipment"]) if o.get("shipment") else None,
        "confirmation": _confirmation(o["latestConfirmation"]) if o.get("latestConfirmation") else None,
        "created_at": o["createdAt"],
        "updated_at": o["updatedAt"],
    }


def _order(o: Json) -> Order:
    return Order(**_order_fields(o))


def _return(r: Json) -> ReturnRequest:
    return ReturnRequest(
        id=r["id"],
        order_id=r["orderId"],
        product_name=r.get("productName"),
        quantity=r["quantity"],
        reason=r["reason"],
        comment=r.get("comment"),
        status=ReturnStatus(r["status"]),
        item_value=_money_or_none(r.get("itemValue")),
        refund_amount=_money_or_none(r.get("refundAmount")),
        created_at=r["createdAt"],
        updated_at=r["updatedAt"],
    )


def _order_detail(o: Json) -> OrderDetail:
    detail = o.get("shipmentDetail")
    return OrderDetail(
        **_order_fields(o),
        subtotal=_money_or_none(o.get("subtotal")),
        shipping_cost=_money_or_none(o.get("shippingCost")),
        discount=_money_or_none(o.get("discountAmount")),
        customer_note=o.get("customerNotes"),
        lines=[
            OrderLine(
                product_name=i["productName"],
                sku=i.get("sku"),
                quantity=i["quantity"],
                unit_price=_money(i["unitPrice"]),
                total_price=_money(i["totalPrice"]),
            )
            for i in o.get("items", [])
        ],
        history=[
            StatusChange(
                from_status=OrderStatus(h["from"]),
                to_status=OrderStatus(h["to"]),
                reason=h.get("reason"),
                at=h["at"],
            )
            for h in o.get("statusHistory", [])
        ],
        notes=[Note(content=n["content"], author=n.get("author"), at=n["createdAt"]) for n in o.get("notes", [])],
        attempts=[
            DeliveryAttempt(
                number=a["attemptNumber"],
                outcome=a["outcome"],
                notes=a.get("notes"),
                rescheduled_for=a.get("rescheduledFor"),
                at=a["at"],
            )
            for a in (detail or {}).get("attempts", [])
        ],
        confirmations=[_confirmation(c) for c in o.get("confirmations", [])],
        returns=[_return(r) for r in o.get("returns", [])],
    )


def _customer_profile(c: Json) -> CustomerProfile:
    r = c.get("reliability") or {}
    orders = c.get("orders") or {}
    return CustomerProfile(
        key=c["phoneKey"],
        user_id=c.get("userId"),
        name=c.get("name"),
        email=c.get("email"),
        risk_score=r.get("riskScore"),
        cod_blocked=bool(r.get("codBlocked")),
        phone_verified=bool(r.get("phoneVerified")),
        delivered_count=r.get("deliveredCount", 0),
        refused_count=r.get("refusedCount", 0),
        returned_count=r.get("returnedCount", 0),
        no_answer_count=r.get("noAnswerCount", 0),
        delivered_value=_money(orders.get("deliveredValue", 0)),
        orders_by_status=orders.get("byStatus", {}),
        first_order_at=orders.get("firstOrderAt"),
        last_order_at=orders.get("lastOrderAt"),
    )


def _cart(c: Json) -> Cart:
    customer = c["customer"]
    return Cart(
        id=c["id"],
        customer=Customer(
            key=customer.get("phone"),  # raw phone; the store normalizes it on lookup
            user_id=customer.get("userId"),
            registered=customer.get("userId") is not None,
            name=customer.get("name"),
            email=customer.get("email"),
            phone=customer.get("phone"),
        ),
        value=_money(c["value"]),
        lines=[
            CartLine(
                product_name=i.get("productName"),
                sku=i.get("sku"),
                quantity=i["quantity"],
                price_when_added=_money(i["priceSnapshot"]),
                price_now=_money_or_none(i.get("currentPrice")),
                stock=i.get("stockQuantity"),
            )
            for i in c.get("items", [])
        ],
        last_activity_at=c["lastActivityAt"],
    )


def _hold(h: Json) -> DispatchHold:
    return DispatchHold(
        order_id=h["orderId"],
        shipment_id=h["shipmentId"],
        shipment_status=_SHIPMENT_STATUS[h["shipmentStatus"]],
        held=h["held"],
        reason=h.get("reason"),
        held_at=h.get("heldAt"),
    )


def _discount(d: Json) -> Discount:
    return Discount(
        id=d["id"],
        code=d["code"],
        percent=d["percent"],
        min_order_value=_money_or_none(d.get("minOrderValue")),
        valid_until=d["validUntil"],
        active=d["active"],
        uses=d.get("uses", 0),
    )


def _message(response: httpx.Response) -> str:
    try:
        return str(response.json().get("message", response.text[:300]))
    except ValueError:
        return response.text[:300]
