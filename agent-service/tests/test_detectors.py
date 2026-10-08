from datetime import timedelta
from decimal import Decimal

from revenueops.cases.models import CaseType, SubjectType
from revenueops.commerce.models import (
    Capabilities,
    LateShipment,
    OrderStatus,
    ReturnRequest,
    ReturnStatus,
    Shipment,
    ShipmentStatus,
)
from revenueops.detectors import run_detectors
from tests.contract import check_adapter_contract
from tests.fakes import NOW, FakeStore, ago, cart, order


def types_by_subject(store: FakeStore) -> dict[str, CaseType]:
    return {c.subject_id: c.case_type for c in run_detectors(store, NOW).candidates}


def test_the_fake_store_passes_the_adapter_contract() -> None:
    check_adapter_contract(FakeStore(orders=[order("o1")]), NOW)


def test_unconfirmed_after_24_hours_only() -> None:
    store = FakeStore(orders=[order("fresh", hours_since_update=23), order("stale", hours_since_update=25)])
    assert types_by_subject(store) == {"stale": CaseType.UNCONFIRMED_ORDER}


def test_refusal_risk_wins_over_unconfirmed_for_the_same_order() -> None:
    store = FakeStore(orders=[order("risky", hours_since_update=30, risk=70)])
    result = run_detectors(store, NOW)
    assert [(c.subject_id, c.case_type) for c in result.candidates] == [("risky", CaseType.REFUSAL_RISK)]
    assert result.candidates[0].signals["risk_score"] == 70


def test_refusal_risk_threshold_is_55() -> None:
    store = FakeStore(orders=[order("a", risk=54), order("b", risk=55)])
    assert types_by_subject(store) == {"b": CaseType.REFUSAL_RISK}


def test_stalled_fulfilment_after_48_hours() -> None:
    store = FakeStore(
        orders=[
            order("c1", OrderStatus.CONFIRMED, hours_since_update=47),
            order("c2", OrderStatus.PROCESSING, hours_since_update=49),
            order("done", OrderStatus.DELIVERED, hours_since_update=500),
        ]
    )
    assert types_by_subject(store) == {"c2": CaseType.STALLED_FULFILMENT}


def test_late_shipments_and_stale_returns() -> None:
    late = LateShipment(
        order_id="o9",
        order_status=OrderStatus.SHIPPED,
        shipment=Shipment(id="s9", status=ShipmentStatus.IN_TRANSIT, cod_amount=Decimal(800), courier_promised_days=2),
        days_since_dispatch=6,
    )
    ret = ReturnRequest(
        id="r1",
        order_id="o8",
        quantity=1,
        reason="damaged",
        status=ReturnStatus.REQUESTED,
        item_value=Decimal(300),
        created_at=ago(72),
        updated_at=ago(72),
    )
    result = run_detectors(FakeStore(late=[late], returns=[ret]), NOW)
    by_id = {c.subject_id: c for c in result.candidates}
    assert by_id["o9"].case_type == CaseType.LATE_SHIPMENT and by_id["o9"].value_at_risk == Decimal(800)
    assert by_id["r1"].case_type == CaseType.STALE_RETURN and by_id["r1"].subject_type == SubjectType.RETURN


def test_abandoned_carts_skip_unreachable_and_ancient_ones() -> None:
    store = FakeStore(
        carts=[
            cart("recent", idle_hours=2),
            cart("abandoned", idle_hours=48),
            cart("no_contact", idle_hours=48, email=None),
            cart("ancient", idle_hours=24 * 31),
        ]
    )
    # The fake's customers have a phone key but no phone, so only email counts as contact here.
    assert types_by_subject(store) == {"abandoned": CaseType.ABANDONED_CART}


def test_detectors_needing_missing_capabilities_are_skipped() -> None:
    store = FakeStore(orders=[order("risky", hours_since_update=30, risk=90)], capabilities=Capabilities())
    result = run_detectors(store, NOW)
    assert result.skipped[CaseType.REFUSAL_RISK] == ("cod_risk_scores",)
    assert CaseType.REFUSAL_RISK not in result.ran
    # Without risk scores the same order is still caught as unconfirmed.
    assert [c.case_type for c in result.candidates] == [CaseType.UNCONFIRMED_ORDER]


def test_most_urgent_and_most_valuable_first() -> None:
    store = FakeStore(
        orders=[
            order("small", hours_since_update=30, total="100"),
            order("big", hours_since_update=30, total="9000"),
            order("risky", risk=80),
        ],
        carts=[cart("cart", idle_hours=48, value="20000")],
    )
    assert [c.subject_id for c in run_detectors(store, NOW).candidates] == ["risky", "big", "small", "cart"]


def test_uses_the_given_clock() -> None:
    store = FakeStore(orders=[order("o", hours_since_update=10)], now=NOW + timedelta(hours=20))
    (candidate,) = run_detectors(store, NOW + timedelta(hours=20)).candidates
    assert candidate.signals["hours_since_update"] == 30
