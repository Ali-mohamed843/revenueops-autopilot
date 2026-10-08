from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from revenueops.adapters.base import StoreAdapter
from revenueops.cases.models import CaseType, SubjectType
from revenueops.commerce.models import Order, OrderStatus


@dataclass(frozen=True)
class Thresholds:
    unconfirmed_after_hours: float = 24
    stalled_after_hours: float = 48
    refusal_risk_score: int = 55  # StoreForge's own "high risk" line
    cart_idle_hours: float = 24
    cart_max_idle_days: float = 30  # older carts are not worth chasing
    return_open_hours: float = 48


@dataclass(frozen=True)
class CaseCandidate:
    case_type: CaseType
    subject_type: SubjectType
    subject_id: str
    title: str
    value_at_risk: Decimal
    currency: str
    priority: int  # 1 = most urgent; when one subject matches several rules, the lowest wins
    signals: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Detector:
    case_type: CaseType
    needs: tuple[str, ...]  # Capabilities flags the store must have
    detect: Callable[[StoreAdapter, Thresholds, datetime], list[CaseCandidate]]


@dataclass
class DetectionResult:
    candidates: list[CaseCandidate]
    ran: list[CaseType]  # detectors that ran; only their cases may be closed as cleared
    skipped: dict[CaseType, tuple[str, ...]]  # detector -> missing capabilities


def _hours(now: datetime, then: datetime) -> float:
    return round((now - then).total_seconds() / 3600, 1)


def _order_signals(o: Order, now: datetime) -> dict[str, Any]:
    signals: dict[str, Any] = {
        "order_status": o.status,
        "hours_since_update": _hours(now, o.updated_at),
        "age_hours": _hours(now, o.created_at),
        "customer_key": o.customer.key,
        "registered_customer": o.customer.registered,
        "governorate": o.governorate,
        "payment_method": o.payment_method,
    }
    if o.shipment:
        signals["risk_score"] = o.shipment.risk_score
        signals["shipment_status"] = o.shipment.status
    if o.confirmation:
        signals["confirmation"] = {
            "status": o.confirmation.status,
            "expired": o.confirmation.expired,
            "reminders_sent": o.confirmation.reminders_sent,
        }
    return signals


def _order_case(o: Order, case_type: CaseType, title: str, priority: int, now: datetime) -> CaseCandidate:
    return CaseCandidate(
        case_type=case_type,
        subject_type=SubjectType.ORDER,
        subject_id=o.id,
        title=title,
        value_at_risk=o.total,
        currency=o.currency,
        priority=priority,
        signals=_order_signals(o, now),
    )


# -------------------------------------------------------------------- rules


def refusal_risk(store: StoreAdapter, t: Thresholds, now: datetime) -> list[CaseCandidate]:
    """A cash-on-delivery order the store itself scores as likely to be refused, not shipped yet."""
    orders = store.list_orders([OrderStatus.PENDING, OrderStatus.CONFIRMED, OrderStatus.PROCESSING])
    return [
        _order_case(
            o, CaseType.REFUSAL_RISK, f"High refusal risk ({o.shipment.risk_score}/100) before dispatch", 1, now
        )
        for o in orders
        if o.shipment and o.shipment.risk_score is not None and o.shipment.risk_score >= t.refusal_risk_score
    ]


def unconfirmed_order(store: StoreAdapter, t: Thresholds, now: datetime) -> list[CaseCandidate]:
    orders = store.list_orders([OrderStatus.PENDING], unchanged_for_hours=t.unconfirmed_after_hours)
    return [
        _order_case(o, CaseType.UNCONFIRMED_ORDER, f"Unconfirmed for {_hours(now, o.updated_at):.0f}h", 2, now)
        for o in orders
    ]


def stalled_fulfilment(store: StoreAdapter, t: Thresholds, now: datetime) -> list[CaseCandidate]:
    orders = store.list_orders(
        [OrderStatus.CONFIRMED, OrderStatus.PROCESSING], unchanged_for_hours=t.stalled_after_hours
    )
    return [
        _order_case(
            o,
            CaseType.STALLED_FULFILMENT,
            f"{o.status.capitalize()} for {_hours(now, o.updated_at):.0f}h, not shipped",
            3,
            now,
        )
        for o in orders
    ]


def late_shipment(store: StoreAdapter, t: Thresholds, now: datetime) -> list[CaseCandidate]:
    candidates = []
    for late in store.list_late_shipments():
        s = late.shipment
        candidates.append(
            CaseCandidate(
                case_type=CaseType.LATE_SHIPMENT,
                subject_type=SubjectType.ORDER,
                subject_id=late.order_id,
                title=f"{late.days_since_dispatch:.0f} days on the road (promised {s.courier_promised_days})",
                value_at_risk=s.cod_amount,
                currency="EGP",
                priority=3,
                signals={
                    "order_status": late.order_status,
                    "courier": s.courier,
                    "days_since_dispatch": late.days_since_dispatch,
                    "promised_days": s.courier_promised_days,
                    "attempt_count": s.attempt_count,
                    "shipment_status": s.status,
                    "governorate": late.governorate,
                },
            )
        )
    return candidates


def abandoned_cart(store: StoreAdapter, t: Thresholds, now: datetime) -> list[CaseCandidate]:
    candidates = []
    for cart in store.list_idle_carts(t.cart_idle_hours):
        idle = _hours(now, cart.last_activity_at)
        reachable = cart.customer.email or cart.customer.phone
        if idle > t.cart_max_idle_days * 24 or not reachable or cart.value <= 0:
            continue
        candidates.append(
            CaseCandidate(
                case_type=CaseType.ABANDONED_CART,
                subject_type=SubjectType.CART,
                subject_id=cart.id,
                title=f"Cart idle for {idle / 24:.1f} days",
                value_at_risk=cart.value,
                currency=cart.currency,
                priority=4,
                signals={
                    "idle_hours": idle,
                    "customer_key": cart.customer.key,
                    "customer_name": cart.customer.name,
                    "has_email": bool(cart.customer.email),
                    "has_phone": bool(cart.customer.phone),
                    "lines": [line.model_dump(mode="json") for line in cart.lines],
                },
            )
        )
    return candidates


def stale_return(store: StoreAdapter, t: Thresholds, now: datetime) -> list[CaseCandidate]:
    return [
        CaseCandidate(
            case_type=CaseType.STALE_RETURN,
            subject_type=SubjectType.RETURN,
            subject_id=r.id,
            title=f"Return open for {_hours(now, r.created_at) / 24:.1f} days ({r.reason})",
            value_at_risk=r.item_value or Decimal(0),
            currency="EGP",
            priority=4,
            signals={
                "order_id": r.order_id,
                "reason": r.reason,
                "product_name": r.product_name,
                "quantity": r.quantity,
                "age_hours": _hours(now, r.created_at),
            },
        )
        for r in store.list_open_returns(t.return_open_hours)
    ]


DETECTORS: tuple[Detector, ...] = (
    Detector(CaseType.REFUSAL_RISK, ("cod_risk_scores",), refusal_risk),
    Detector(CaseType.UNCONFIRMED_ORDER, (), unconfirmed_order),
    Detector(CaseType.STALLED_FULFILMENT, (), stalled_fulfilment),
    Detector(CaseType.LATE_SHIPMENT, ("delivery_tracking",), late_shipment),
    Detector(CaseType.ABANDONED_CART, ("abandoned_carts",), abandoned_cart),
    Detector(CaseType.STALE_RETURN, ("returns",), stale_return),
)


def run_detectors(
    store: StoreAdapter,
    now: datetime,
    thresholds: Thresholds | None = None,
    detectors: tuple[Detector, ...] = DETECTORS,
) -> DetectionResult:
    """Run every detector the store supports; keep the most urgent candidate per subject."""
    t = thresholds or Thresholds()
    best: dict[tuple[SubjectType, str], CaseCandidate] = {}
    ran: list[CaseType] = []
    skipped: dict[CaseType, tuple[str, ...]] = {}

    for d in detectors:
        missing = tuple(f for f in d.needs if not store.capabilities.has(f))
        if missing:
            skipped[d.case_type] = missing
            continue
        ran.append(d.case_type)
        for c in d.detect(store, t, now):
            key = (c.subject_type, c.subject_id)
            if key not in best or c.priority < best[key].priority:
                best[key] = c

    candidates = sorted(best.values(), key=lambda c: (c.priority, -c.value_at_risk))
    return DetectionResult(candidates=candidates, ran=ran, skipped=skipped)
