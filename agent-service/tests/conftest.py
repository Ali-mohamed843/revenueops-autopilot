from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import revenueops.cases.models  # noqa: F401  (registers tables)
from revenueops.db import Base, session_factory


@pytest.fixture
def session() -> Iterator[Session]:
    """A fresh in-memory SQLite database with the schema, shared across threads (the API test client)."""
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn: Any, _: Any) -> None:
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with session_factory(engine)() as s:
        yield s
