from typing import Annotated

from fastapi import Depends, FastAPI, Response, status
from sqlalchemy import Engine

from revenueops import __version__
from revenueops.db import database_is_up, get_engine

app = FastAPI(title="RevenueOps Autopilot", version=__version__)


@app.get("/health")
def health(response: Response, engine: Annotated[Engine, Depends(get_engine)]) -> dict[str, str]:
    """Liveness plus a database check; 503 when the database is unreachable."""
    if database_is_up(engine):
        return {"status": "ok", "database": "ok"}
    response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "degraded", "database": "unavailable"}
