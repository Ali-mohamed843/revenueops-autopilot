from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine

from revenueops.db import get_engine
from revenueops.main import app


@pytest.fixture
def client() -> Iterator[TestClient]:
    yield TestClient(app)
    app.dependency_overrides.clear()


def use_engine(engine: Engine) -> None:
    app.dependency_overrides[get_engine] = lambda: engine


def test_health_ok_when_database_answers(client: TestClient) -> None:
    use_engine(create_engine("sqlite://"))

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


def test_health_503_when_database_is_unreachable(client: TestClient) -> None:
    # Nothing listens on port 1, so the connection is refused immediately.
    use_engine(create_engine("postgresql+psycopg://user:pass@127.0.0.1:1/none", connect_args={"connect_timeout": 1}))

    response = client.get("/health")

    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "database": "unavailable"}
