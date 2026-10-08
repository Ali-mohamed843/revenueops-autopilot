from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from revenueops.db import get_session
from revenueops.main import app
from revenueops.pipeline import detect
from tests.fakes import FakeStore, order


@pytest.fixture
def client(session: Session) -> Iterator[TestClient]:
    detect(
        session,
        FakeStore(orders=[order("risky", risk=90, total="700"), order("late", hours_since_update=30, total="300")]),
    )
    app.dependency_overrides[get_session] = lambda: session
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_lists_cases_most_urgent_first(client: TestClient) -> None:
    body = client.get("/cases").json()
    assert [c["subject_id"] for c in body] == ["risky", "late"]
    assert body[0]["case_type"] == "refusal_risk"
    assert body[0]["value_at_risk"] == "700.00"


def test_filters_by_type_and_status(client: TestClient) -> None:
    assert [c["subject_id"] for c in client.get("/cases?case_type=unconfirmed_order").json()] == ["late"]
    assert client.get("/cases?status=closed").json() == []
    assert client.get("/cases?case_type=nonsense").status_code == 422


def test_case_detail_includes_events(client: TestClient) -> None:
    case_id = client.get("/cases").json()[0]["id"]
    body = client.get(f"/cases/{case_id}").json()
    assert body["investigation"] is None
    assert [e["type"] for e in body["events"]] == ["detected"]
    assert client.get("/cases/00000000-0000-4000-8000-000000000000").status_code == 404
