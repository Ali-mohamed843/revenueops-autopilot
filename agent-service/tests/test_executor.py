"""The executor: what runs on its own, what waits for people, and how actions are undone."""

from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from revenueops.agents.messenger import MessageBrief
from revenueops.cases.models import Case, CaseStatus
from revenueops.commerce.models import ReturnRequest, ReturnStatus
from revenueops.executor.engine import (
    ExecutorError,
    act,
    approve,
    complete,
    executions_of,
    reject,
    rollback,
)
from revenueops.executor.models import Execution, ExecutionStatus, OutboxStatus
from revenueops.pipeline import detect, plan_cases
from tests.fakes import NOW, FakeLLM, FakeStore, ago, cart, order, tool_use

briefs: list[MessageBrief] = []


def write(brief: MessageBrief) -> tuple[str, str, str]:
    briefs.append(brief)
    return brief.language, "رسالة " + " ".join(brief.must_include), "test-writer"


def p(action: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"action": action, "params": params or {}, "rationale": "Fits the case.", "policy_refs": []}


def planned(session: Session, store: FakeStore, *proposals: dict[str, Any], confidence: float = 0.85) -> Case:
    detect(session, store)
    case = session.scalars(select(Case)).one()
    case.investigation = {"report": {"confidence": confidence}}
    case.status = CaseStatus.INVESTIGATED
    session.commit()
    strategy = {"approach": "Recover it.", "proposals": list(proposals)}
    plan_cases(session, store, FakeLLM([[tool_use("submit_strategy", strategy)]]), [case])
    assert case.status == CaseStatus.PLANNED, case.events[-1].data
    return case


def unconfirmed(session: Session, total: str = "1000") -> tuple[Case, FakeStore]:
    store = FakeStore(orders=[order("o1", hours_since_update=30, total=total)])
    case = planned(
        session,
        store,
        p("send_confirmation_reminder", {"channel": "whatsapp"}),
        p("hold_dispatch"),
        p("request_phone_confirmation"),
    )
    return case, store


def held(store: FakeStore) -> bool:
    shipment = store.orders[0].shipment
    assert shipment is not None
    return shipment.dispatch_hold


# ------------------------------------------------------------ running alone


def test_auto_actions_run_and_leave_a_full_record(session: Session) -> None:
    case, store = unconfirmed(session)
    run = act(session, store, write, [case], now=NOW)

    (ex,) = executions_of(session, case)
    assert run.ran == [ex.id]
    assert (ex.action_type, ex.tier, ex.status, ex.decided_by) == (
        "send_confirmation_reminder",
        "auto",
        "succeeded",
        "auto",
    )
    assert ex.confidence == 0.85 and ex.before == ex.after
    (msg,) = ex.messages
    assert (msg.channel, msg.recipient, msg.status, msg.drafted_by) == (
        "whatsapp",
        "01099000001",
        "queued",
        "test-writer",
    )
    assert "#O1" in msg.body  # the order reference the Messenger had to include
    assert case.status == CaseStatus.ACTED
    assert [e.type for e in case.events][-2:] == ["action_requested", "action_succeeded"]
    assert store.notes == [
        ("o1", f"RevenueOps Autopilot: Send a confirmation reminder (case {case.id})", "RevenueOps Autopilot")
    ]


def test_actions_run_one_at_a_time_and_escalate_when_ready(session: Session) -> None:
    case, store = unconfirmed(session)
    act(session, store, write, [case], now=NOW)  # the reminder
    act(session, store, write, [case], now=NOW)  # the hold: ready and auto, so it runs too
    assert held(store)
    hold = executions_of(session, case)[1]
    assert hold.before is not None and hold.after is not None
    assert (hold.action_type, hold.before["dispatch_hold"], hold.after["dispatch_hold"]) == (
        "hold_dispatch",
        False,
        True,
    )

    assert act(session, store, write, [case], now=NOW + timedelta(hours=23)).waiting == [case.id]  # call not ready
    run = act(session, store, write, [case], now=NOW + timedelta(hours=24))
    call = session.get(Execution, run.queued[0])
    assert call is not None
    assert (call.action_type, call.status, call.tier) == ("request_phone_confirmation", "awaiting_human", "human_only")
    assert case.status == CaseStatus.ACTING
    assert act(session, store, write, [case], now=NOW + timedelta(hours=25)).ran == []  # nothing while one waits

    complete(session, call.id, by="sara", succeeded=True, note="Buyer confirmed by phone")
    assert (call.status, call.decided_by, call.result) == ("succeeded", "sara", {"outcome": "Buyer confirmed by phone"})
    assert case.status == CaseStatus.ACTED


# --------------------------------------------------------------- approvals


def test_approval_actions_wait_and_run_only_when_approved(session: Session) -> None:
    store = FakeStore(orders=[order("o1", total="20000", risk=90)])
    case = planned(session, store, p("hold_dispatch"))
    run = act(session, store, write, [case], now=NOW)
    ex = session.get(Execution, run.queued[0])
    assert ex is not None and (ex.tier, ex.status) == ("approval", "pending_approval")
    assert not held(store)  # nothing happened yet

    approve(session, store, write, ex.id, by="omar", note="Large order, hold it", now=NOW)
    assert (ex.status, ex.decided_by, ex.decision_note) == ("succeeded", "omar", "Large order, hold it")
    assert held(store)
    assert [e.type for e in case.events][-3:] == ["action_requested", "action_approved", "action_succeeded"]


def test_approve_refuses_what_now_needs_a_person(session: Session) -> None:
    store = FakeStore(returns=[_return("r1", "250")])
    case = planned(session, store, p("process_return", {"decision": "approve"}))
    ex = session.get(Execution, act(session, store, write, [case], now=NOW).queued[0])
    assert ex is not None and ex.tier == "approval"
    case.value_at_risk = Decimal(400)  # the refund would now be over 300 EGP (RET-2)
    with pytest.raises(ExecutorError, match="needs a person.*300 EGP"):
        approve(session, store, write, ex.id, by="omar")
    assert ex.status == "pending_approval" and store.returns[0].status == ReturnStatus.REQUESTED


def test_approve_refuses_once_the_case_is_closed(session: Session) -> None:
    store = FakeStore(orders=[order("o1", total="20000", risk=90)])
    case = planned(session, store, p("hold_dispatch"))
    ex = session.get(Execution, act(session, store, write, [case], now=NOW).queued[0])
    assert ex is not None
    store.orders = []  # the buyer confirmed: the next detection closes the case
    detect(session, store)
    assert case.status == CaseStatus.CLOSED
    with pytest.raises(ExecutorError, match="Case closed"):
        approve(session, store, write, ex.id, by="omar")
    assert ex.status == "rejected" and ex.decided_by == "system"


def test_human_only_actions_never_run_through_approve(session: Session) -> None:
    store = FakeStore(orders=[order("o1", risk=90)])
    case = planned(session, store, p("request_phone_confirmation"))  # refusal risk: a call at once (COD-2)
    ex = session.get(Execution, act(session, store, write, [case], now=NOW).queued[0])
    assert ex is not None and ex.status == ExecutionStatus.AWAITING_HUMAN
    with pytest.raises(ExecutorError, match="not waiting for approval"):
        approve(session, store, write, ex.id, by="omar")
    with pytest.raises(ExecutorError, match="can't be undone from here"):
        complete(session, ex.id, by="sara", succeeded=True, note="Confirmed")
        rollback(session, store, ex.id, by="sara", reason="x")


def test_rejecting_frees_the_case_for_its_next_action(session: Session) -> None:
    store = FakeStore(orders=[order("o1", hours_since_update=30, total="20000")])
    case = planned(session, store, p("hold_dispatch"), p("send_confirmation_reminder", {"channel": "sms"}))
    # The reminder is auto and ranks first; once it ran, the large-order hold waits for approval.
    act(session, store, write, [case], now=NOW)
    hold = session.get(Execution, act(session, store, write, [case], now=NOW).queued[0])
    assert hold is not None and hold.action_type == "hold_dispatch"
    reject(session, hold.id, by="omar", note="Not needed")
    assert (hold.status, case.status) == ("rejected", CaseStatus.PLANNED)
    assert act(session, store, write, [case], now=NOW).waiting == [case.id]  # nothing is retried
    with pytest.raises(ExecutorError, match="only waiting actions"):
        reject(session, hold.id, by="omar")


def test_each_proposed_action_runs_at_most_once(session: Session) -> None:
    case, store = unconfirmed(session)
    act(session, store, write, [case], now=NOW)
    first = executions_of(session, case)[0]
    session.add(
        Execution(
            case_id=case.id,
            case_action_id=first.case_action_id,
            action_type=first.action_type,
            params={},
            tier="auto",
            status="succeeded",
            reversible=False,
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()


# ----------------------------------------------------------------- failures


def test_a_failed_action_is_recorded_and_the_next_one_can_run(session: Session) -> None:
    store = FakeStore(orders=[order("o1", hours_since_update=30)])
    store.orders = [
        store.orders[0].model_copy(update={"customer": store.orders[0].customer.model_copy(update={"phone": None})})
    ]
    case = planned(session, store, p("send_confirmation_reminder", {"channel": "sms"}), p("hold_dispatch"))
    run = act(session, store, write, [case], now=NOW)
    (ex,) = executions_of(session, case)
    assert run.failed == [ex.id]
    assert (ex.status, ex.error) == ("failed", "No phone number to send a sms message to")
    assert case.status == CaseStatus.PLANNED and ex.messages == []
    act(session, store, write, [case], now=NOW)
    assert held(store)


# ------------------------------------------------------------------- undo


def test_rollback_releases_a_hold(session: Session) -> None:
    store = FakeStore(orders=[order("o1", risk=90)])
    case = planned(session, store, p("hold_dispatch"))
    ex = session.get(Execution, act(session, store, write, [case], now=NOW).ran[0])
    assert ex is not None and held(store)
    rollback(session, store, ex.id, by="omar", reason="Buyer confirmed by phone")
    assert not held(store)
    assert (ex.status, ex.rolled_back_by) == ("rolled_back", "omar")
    assert ex.rollback_result == {"dispatch_hold": False, "messages_cancelled": 0, "reason": "Buyer confirmed by phone"}
    assert case.status == CaseStatus.PLANNED and case.events[-1].type == "action_rolled_back"
    assert store.notes[-1][1] == "RevenueOps Autopilot: undid Hold dispatch (omar: Buyer confirmed by phone)"
    with pytest.raises(ExecutorError, match="only a succeeded action"):
        rollback(session, store, ex.id, by="omar", reason="again")


def test_rollback_voids_a_discount_and_cancels_its_message(session: Session) -> None:
    store = FakeStore(carts=[cart("c1", idle_hours=48, value="1000")])
    case = planned(
        session,
        store,
        p("send_cart_reminder", {"channel": "whatsapp"}),
        p("offer_discount", {"percent": 10, "valid_hours": 48}),
    )
    act(session, store, write, [case], now=NOW)  # the reminder; the discount waits 24h (DISC-5)
    ex = session.get(Execution, act(session, store, write, [case], now=NOW + timedelta(hours=24)).ran[0])
    assert ex is not None and ex.action_type == "offer_discount"
    assert ex.result == {"discount_id": "d1", "code": "RO-TEST0001"}
    assert "RO-TEST0001" in ex.messages[0].body and ex.messages[0].recipient == "01099000002"
    rollback(session, store, ex.id, by="omar", reason="Sent by mistake")
    assert store.discounts["d1"].active is False
    assert ex.messages[0].status == OutboxStatus.CANCELLED


def test_a_used_discount_cannot_be_rolled_back(session: Session) -> None:
    store = FakeStore(carts=[cart("c1", idle_hours=48, value="1000")])
    case = planned(
        session,
        store,
        p("send_cart_reminder", {"channel": "sms"}),
        p("offer_discount", {"percent": 10, "valid_hours": 48}),
    )
    act(session, store, write, [case], now=NOW)
    ex = session.get(Execution, act(session, store, write, [case], now=NOW + timedelta(hours=24)).ran[0])
    assert ex is not None and ex.action_type == "offer_discount"
    store.discounts["d1"] = store.discounts["d1"].model_copy(update={"uses": 1})
    with pytest.raises(ExecutorError, match="store refused"):
        rollback(session, store, ex.id, by="omar", reason="too late")
    assert ex.status == "succeeded"


def test_message_only_actions_roll_back_by_cancelling_the_message(session: Session) -> None:
    case, store = unconfirmed(session)
    ex = session.get(Execution, act(session, store, write, [case], now=NOW).ran[0])
    assert ex is not None
    rollback(session, store, ex.id, by="omar", reason="Wrong customer")
    assert ex.messages[0].status == OutboxStatus.CANCELLED and ex.status == "rolled_back"


def test_return_decisions_cannot_be_rolled_back(session: Session) -> None:
    store = FakeStore(returns=[_return("r1", "250")])
    case = planned(session, store, p("process_return", {"decision": "approve", "refund_amount": 250}))
    ex = session.get(Execution, act(session, store, write, [case], now=NOW).queued[0])
    assert ex is not None
    approve(session, store, write, ex.id, by="omar", now=NOW)
    assert store.returns[0].status == ReturnStatus.APPROVED
    with pytest.raises(ExecutorError, match="can't be undone"):
        rollback(session, store, ex.id, by="omar", reason="oops")


def _return(id_: str, value: str) -> ReturnRequest:
    return ReturnRequest(
        id=id_,
        order_id="o9",
        quantity=1,
        reason="damaged",
        status=ReturnStatus.REQUESTED,
        item_value=Decimal(value),
        created_at=ago(72),
        updated_at=ago(72),
    )


@pytest.mark.parametrize(
    ("phone", "ok"), [("rtreret", False), ("12", False), ("010 9900-0001", True), ("+201099000001", True)]
)
def test_messages_only_go_to_real_phone_numbers(session: Session, phone: str, ok: bool) -> None:
    store = FakeStore(orders=[order("o1", hours_since_update=30)])
    o = store.orders[0]
    store.orders = [o.model_copy(update={"customer": o.customer.model_copy(update={"phone": phone})})]
    case = planned(session, store, p("send_confirmation_reminder", {"channel": "sms"}))
    act(session, store, write, [case], now=NOW)
    (ex,) = executions_of(session, case)
    assert (ex.status == "succeeded") == ok, ex.error
    if ok:
        assert ex.messages[0].recipient == phone.replace(" ", "").replace("-", "")
    else:
        assert ex.error is not None and "is not a phone number" in ex.error
