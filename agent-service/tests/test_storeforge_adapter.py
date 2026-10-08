"""StoreForgeAdapter against recorded real responses (tests/fixtures/storeforge, see record.py there)."""

import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest

from revenueops.adapters.base import NotFound, StoreError
from revenueops.adapters.storeforge import StoreForgeAdapter
from revenueops.commerce.models import ConfirmationStatus, OrderStatus, ShipmentStatus
from tests.contract import check_adapter_contract

FIXTURES = Path(__file__).parent / "fixtures" / "storeforge"
API_KEY = "test-key"


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


ORDER_DETAIL = fixture("order_detail")


def recorded_api(request: httpx.Request) -> httpx.Response:
    """Serves the recorded responses, like StoreForge would."""
    if request.headers.get("x-api-key") != API_KEY:
        return httpx.Response(401, json={"message": "Invalid or missing API key"})
    path = request.url.path.removeprefix("/api/integration")
    if path == "/orders":
        return httpx.Response(200, json=fixture("orders"))
    if path.startswith("/orders/"):
        order_id = path.split("/")[2]
        listed = {o["id"]: o for o in fixture("orders")["items"]}
        if order_id == ORDER_DETAIL["id"]:
            return httpx.Response(200, json=ORDER_DETAIL)
        if order_id in listed:  # detail of a listed order: the summary plus empty collections
            empty: dict[str, list[Any]] = {
                "items": [],
                "statusHistory": [],
                "notes": [],
                "confirmations": [],
                "returns": [],
            }
            return httpx.Response(200, json={**listed[order_id], **empty, "shipmentDetail": None})
        return httpx.Response(404, json={"message": "Order not found"})
    if path.startswith("/customers/"):
        return httpx.Response(200, json=fixture("customer"))
    if path == "/carts":
        return httpx.Response(200, json=fixture("carts"))
    if path == "/shipments":
        return httpx.Response(200, json=fixture("late_shipments"))
    if path == "/returns":
        return httpx.Response(200, json=fixture("returns"))
    return httpx.Response(404, json={"message": "no route"})


def make(handler: Any = recorded_api, key: str = API_KEY) -> StoreForgeAdapter:
    return StoreForgeAdapter("http://storeforge.test/api", key, transport=httpx.MockTransport(handler))


def test_passes_the_adapter_contract() -> None:
    check_adapter_contract(make(), now=datetime.now().astimezone())


def test_sends_the_api_key_and_maps_statuses() -> None:
    orders = make().list_orders([OrderStatus.PENDING, OrderStatus.CONFIRMED, OrderStatus.PROCESSING])
    assert len(orders) == len(fixture("orders")["items"])
    first = orders[0]
    raw = fixture("orders")["items"][0]
    assert first.id == raw["id"]
    assert first.total == Decimal(str(raw["total"]))
    assert first.customer.key == raw["customer"]["phoneKey"]
    assert first.shipment is not None and first.shipment.status == ShipmentStatus.NOT_DISPATCHED  # "pending"
    assert first.shipment.risk_score == raw["shipment"]["riskScoreAtDispatch"]


def test_order_detail_maps_history_attempts_and_confirmations() -> None:
    d = make().get_order(ORDER_DETAIL["id"])
    assert d.status == OrderStatus.CANCELLED
    assert d.shipment is not None and d.shipment.status == ShipmentStatus.RETURNED
    assert d.shipment.return_reason == ORDER_DETAIL["shipmentDetail"]["rtoReason"]
    assert [a.outcome for a in d.attempts] == [a["outcome"] for a in ORDER_DETAIL["shipmentDetail"]["attempts"]]
    assert [h.to_status for h in d.history] == [OrderStatus(h["to"]) for h in ORDER_DETAIL["statusHistory"]]
    assert [c.status for c in d.confirmations] == [
        ConfirmationStatus(c["status"]) for c in ORDER_DETAIL["confirmations"]
    ]
    assert sum(line.total_price for line in d.lines) == Decimal(str(ORDER_DETAIL["subtotal"]))


def test_customer_profile() -> None:
    raw = fixture("customer")
    p = make().get_customer(raw["phoneKey"])
    assert p is not None
    assert p.key == raw["phoneKey"]
    assert p.risk_score == raw["reliability"]["riskScore"]
    assert p.refused_count == raw["reliability"]["refusedCount"]
    assert p.orders_by_status == raw["orders"]["byStatus"]


def test_carts_shipments_and_returns() -> None:
    store = make()
    carts = store.list_idle_carts(24)
    assert [c.id for c in carts] == [c["id"] for c in fixture("carts")["items"]]
    assert carts[0].lines[0].price_when_added == Decimal(str(fixture("carts")["items"][0]["items"][0]["priceSnapshot"]))

    late = store.list_late_shipments()
    assert [s.order_id for s in late] == [s["orderId"] for s in fixture("late_shipments")["items"]]
    assert all(s.shipment.status in (ShipmentStatus.IN_TRANSIT, ShipmentStatus.FAILED_ATTEMPT) for s in late)

    returns = store.list_open_returns(48)
    assert [r.id for r in returns] == [r["id"] for r in fixture("returns")["items"]]


def test_follows_pagination() -> None:
    items = fixture("orders")["items"]
    seen_pages: list[str] = []

    def paged(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        seen_pages.append(str(page))
        assert request.url.params["status"] == "pending"
        assert request.url.params["unchangedForHours"] == "24"
        body = {"items": [items[page - 1]], "total": 2, "page": page, "limit": 100, "pages": 2}
        return httpx.Response(200, json=body)

    orders = make(paged).list_orders([OrderStatus.PENDING], unchanged_for_hours=24)
    assert [o.id for o in orders] == [items[0]["id"], items[1]["id"]]
    assert seen_pages == ["1", "2"]


def test_errors() -> None:
    with pytest.raises(StoreError, match="HTTP 401"):
        make(key="wrong").list_orders([OrderStatus.PENDING])
    with pytest.raises(NotFound):
        make().get_order("00000000-0000-4000-8000-000000000000")

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with pytest.raises(StoreError, match="unreachable"):
        make(down).list_orders([OrderStatus.PENDING])


def test_unknown_customer_key_is_none() -> None:
    def bad_phone(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"message": "Not a valid phone number"})

    assert make(bad_phone).get_customer("12") is None


def test_add_order_note_posts_content_and_author() -> None:
    sent: list[dict[str, Any]] = []

    def capture(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST" and request.url.path.endswith("/orders/o1/notes")
        sent.append(json.loads(request.content))
        return httpx.Response(201, json={"id": "n1"})

    make(capture).add_order_note("o1", "Called the buyer", "RevenueOps Autopilot")
    assert sent == [{"content": "Called the buyer", "author": "RevenueOps Autopilot"}]
