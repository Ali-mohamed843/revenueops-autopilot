from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from revenueops.cases.models import Case, CaseStatus, CaseType
from revenueops.cases.sync import sync_cases
from revenueops.commerce.models import Capabilities
from revenueops.detectors import run_detectors
from tests.fakes import NOW, FakeStore, order


def sync(session: Session, store: FakeStore, hours_later: float = 0):  # type: ignore[no-untyped-def]
    now = NOW + timedelta(hours=hours_later)
    store.now = now
    return sync_cases(session, store.name, run_detectors(store, now), now)


def cases(session: Session) -> list[Case]:
    return list(session.scalars(select(Case).order_by(Case.subject_id)))


def test_opens_one_case_per_subject_with_an_event(session: Session) -> None:
    store = FakeStore(orders=[order("o1", hours_since_update=30), order("o2", hours_since_update=40)])
    report = sync(session, store)
    assert report.opened == 2
    (c1, c2) = cases(session)
    assert c1.status == CaseStatus.OPEN and c1.case_type == CaseType.UNCONFIRMED_ORDER
    assert [e.type for e in c1.events] == ["detected"]


def test_rerunning_is_idempotent_and_refreshes(session: Session) -> None:
    store = FakeStore(orders=[order("o1", hours_since_update=30)])
    sync(session, store)
    report = sync(session, store, hours_later=5)
    assert (report.opened, report.updated, report.closed) == (0, 1, 0)
    (case,) = cases(session)
    assert case.signals["hours_since_update"] == 35
    assert case.title == "Unconfirmed for 35h"


def test_reclassifies_when_a_more_urgent_rule_matches(session: Session) -> None:
    store = FakeStore(orders=[order("o1", hours_since_update=30, risk=10)])
    sync(session, store)
    store.orders = [order("o1", hours_since_update=30, risk=80)]
    report = sync(session, store)
    assert report.reclassified == 1
    (case,) = cases(session)
    assert case.case_type == CaseType.REFUSAL_RISK
    assert [e.type for e in case.events] == ["detected", "reclassified"]


def test_closes_cases_whose_condition_cleared(session: Session) -> None:
    store = FakeStore(orders=[order("o1", hours_since_update=30)])
    sync(session, store)
    store.orders = []  # the customer confirmed: no longer pending
    report = sync(session, store)
    assert report.closed == 1
    (case,) = cases(session)
    assert case.status == CaseStatus.CLOSED and case.close_reason == "condition_cleared"


def test_never_closes_cases_of_a_detector_that_did_not_run(session: Session) -> None:
    store = FakeStore(orders=[order("o1", risk=90)])
    sync(session, store)
    store.capabilities = Capabilities()  # the refusal-risk detector can't run any more
    report = sync(session, store)
    assert report.closed == 0
    assert cases(session)[0].status == CaseStatus.OPEN


def test_a_closed_subject_can_get_a_new_case(session: Session) -> None:
    store = FakeStore(orders=[order("o1", hours_since_update=30)])
    sync(session, store)
    store.orders = []
    sync(session, store)
    store.orders = [order("o1", hours_since_update=30)]
    report = sync(session, store)
    assert report.opened == 1
    assert sorted(c.status for c in cases(session)) == [CaseStatus.CLOSED, CaseStatus.OPEN]


def test_database_rejects_two_open_cases_for_one_subject(session: Session) -> None:
    sync(session, FakeStore(orders=[order("o1", hours_since_update=30)]))
    (existing,) = cases(session)
    session.add(
        Case(
            store=existing.store,
            case_type=CaseType.STALLED_FULFILMENT,
            subject_type=existing.subject_type,
            subject_id="o1",
            title="duplicate",
            value_at_risk=1,
            currency="EGP",
            priority=3,
            signals={},
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()


def test_money_is_stored_to_the_cent(session: Session) -> None:
    sync(session, FakeStore(orders=[order("o1", hours_since_update=30, total="700")]))
    (case,) = cases(session)
    assert str(case.value_at_risk) == "700.00"  # same before and after a reload from the database
