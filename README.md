# RevenueOps Autopilot

AI agents that find revenue at risk in an e-commerce store, decide the safest
action with the most value, act on low-risk cases automatically, and send risky
ones to a human — with every decision recorded and reversible.

The first target is cash-on-delivery commerce in Egypt, where orders are lost to
customers who refuse at the door, orders nobody confirms, shipments that stall,
and carts that are abandoned. The first store is
[StoreForge](https://github.com/Ali-mohamed843/ecommerce-saas); the design is
platform-agnostic, with one adapter per store.

> Status: Phase 2 — the service detects revenue-at-risk cases in a live store
> and an Investigator agent explains each one. Action proposals, approvals and
> execution come next.

## How it fits together

```
StoreForge integration API ──HTTP──▶ adapters/storeforge ──▶ generic commerce models
                                                                   │
                         detectors (plain rules) ──▶ cases (Postgres, audit trail)
                                                                   │
                         Investigator agent (LLM + read-only tools) ──▶ report on the case
```

The design rule: the language model investigates and proposes; deterministic
code decides what may run automatically. The model never approves money or
irreversible actions on its own.

**Platform-agnostic.** Only `adapters/` knows about a specific store. Everything
else works with `commerce/models.py`, and each adapter declares its
`Capabilities`; a detector that needs a feature the store lacks is skipped.
`lint-imports` enforces this in CI, and every adapter must pass the shared
contract in `tests/contract.py`.

### Case types

| Case | Detected when | Priority |
|---|---|---|
| `refusal_risk` | unshipped COD order the store scores ≥ 55/100 for refusal | 1 |
| `unconfirmed_order` | pending for 24 h or more | 2 |
| `stalled_fulfilment` | confirmed or processing for 48 h, not shipped | 3 |
| `late_shipment` | on the road past the courier's promised days | 3 |
| `abandoned_cart` | idle 24 h – 30 days, customer reachable | 4 |
| `stale_return` | return request untouched for 48 h | 4 |

A subject (order, cart, return) has at most one open case; the most urgent rule
wins. Re-running detection updates cases and closes those whose condition
cleared.

## Run it locally

Requirements: [uv](https://docs.astral.sh/uv/), Docker, and StoreForge running
with `INTEGRATION_API_KEY` set in its `.env`.

```bash
docker compose up -d postgres
cd agent-service
cp .env.example .env      # then fill in STOREFORGE_API_KEY and a model key
uv sync
uv run alembic upgrade head
uv run revenueops detect
uv run revenueops investigate --limit 3
uv run uvicorn revenueops.main:app --reload --port 8000
```

- `revenueops detect` scans the store and opens, updates or closes cases.
- `revenueops investigate` runs the Investigator on the most urgent open cases
  (`--case <id>` for one).
- The API serves `GET /health`, `GET /cases` and `GET /cases/{id}`; docs at
  http://localhost:8000/docs.

### The model

Same setup as claude-agent-cli:
set `ANTHROPIC_API_KEY` for Claude, or `ANTHROPIC_BASE_URL=https://openrouter.ai/api`
plus `ANTHROPIC_AUTH_TOKEN` for OpenRouter, and `AGENT_MODEL`. Claude models get
adaptive thinking; other models get only the core Messages API fields.

For an OpenAI-compatible API instead (for example CodeCraft), set
`LLM_PROVIDER=openai`, `OPENAI_BASE_URL` and `OPENAI_API_KEY`; the service
translates its tool calls to the chat-completions format.

Demo data lives on the StoreForge side: `npm run seed:demo` there creates about
1,700 COD orders with realistic delivery histories and open cases.

## Checks

```bash
cd agent-service
uv run ruff format --check .
uv run ruff check .
uv run mypy
uv run lint-imports
uv run pytest
```

CI runs the same checks on Ubuntu and Windows. Tests need no network or API
key: the StoreForge adapter is tested against recorded real responses
(`tests/fixtures/storeforge`, re-record with `record.py` there), and the agents
against a scripted model.

## Layout

```
agent-service/
  src/revenueops/
    commerce/       generic store models and capabilities
    adapters/       base.py (the contract), registry.py, storeforge/
    detectors/      the case rules
    cases/          case and audit-event tables, idempotent sync
    agents/         LLM client, tool loop, Investigator
    pipeline.py     detect, then investigate
    cli.py, main.py command line and HTTP API
  migrations/       Alembic
  tests/
docker-compose.yml  local Postgres on port 5433
```
