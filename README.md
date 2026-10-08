# RevenueOps Autopilot

AI agents that find revenue at risk in an e-commerce store, decide the safest
action with the most value, act on low-risk cases automatically, and send risky
ones to a human — with every decision recorded and reversible.

The first target is cash-on-delivery commerce in Egypt, where orders are lost to
customers who refuse at the door, orders nobody confirms, shipments that stall,
and carts that are abandoned. The first store is
[StoreForge](https://github.com/Ali-mohamed843/ecommerce-saas); the design is
platform-agnostic, with one adapter per store.

> Status: Phase 0 — foundations. The agent service runs and reports health; no
> agents yet.

## How it fits together

```
StoreForge API (NestJS)  ──HTTP──▶  agent-service (Python, FastAPI)  ──▶  Postgres
                                     detectors → agents → decision engine
                                     → approvals → executor → audit log
                                                     ▲
                                     dashboard (Next.js, later)
```

The design rule: the language model investigates and proposes actions; a
deterministic engine scores them and decides what may run automatically. The
model never approves money or irreversible actions on its own.

## Run it locally

Requirements: [uv](https://docs.astral.sh/uv/) and Docker.

```bash
docker compose up -d postgres
cd agent-service
cp .env.example .env
uv sync
uv run uvicorn revenueops.main:app --reload --port 8000
```

Then open http://localhost:8000/health — it returns `{"status": "ok", "database": "ok"}`
when Postgres is reachable, and HTTP 503 when it isn't. API docs are at
http://localhost:8000/docs.

Demo data lives on the StoreForge side: `npm run seed:demo` there creates about
1,700 COD orders with realistic delivery histories and open cases.

## Checks

```bash
cd agent-service
uv run ruff format --check .
uv run ruff check .
uv run mypy
uv run pytest
```

CI runs the same checks on Ubuntu and Windows.

## Layout

```
agent-service/      Python 3.12 service (FastAPI, SQLAlchemy, Postgres)
  src/revenueops/   application code
  tests/
docker-compose.yml  local Postgres on port 5433
```
