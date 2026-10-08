# RevenueOps Autopilot

AI agents that find revenue at risk in an e-commerce store, decide the safest
action with the most value, act on low-risk cases automatically, and send risky
ones to a human — with every decision recorded and reversible.

The first target is cash-on-delivery commerce in Egypt, where orders are lost to
customers who refuse at the door, orders nobody confirms, shipments that stall,
and carts that are abandoned. The first store is
[StoreForge](https://github.com/Ali-mohamed843/ecommerce-saas); the design is
platform-agnostic, with one adapter per store.

> Status: Phase 4 — the service detects revenue-at-risk cases in a live store,
> investigates and plans them with agents, and carries the actions out: `auto`
> actions run on their own, the rest wait for a person, every step is audited
> and undoable actions can be rolled back. A dashboard comes next.

## How it fits together

```
StoreForge integration API ──HTTP──▶ adapters/storeforge ──▶ generic commerce models
                                                                   │
                         detectors (plain rules) ──▶ cases (Postgres, audit trail)
                                                                   │
                         Investigator agent (LLM + read-only tools) ──▶ report on the case
                                                                   │
                         Strategist agent (LLM, fixed action catalogue, policies) ──▶ proposals
                                                                   │
                         decision engine (plain code) ──▶ expected value + tier per action
                                                                   │
                         executor ──▶ auto: run now │ approval: queue │ human_only: a person does it
                                   └─▶ store actions, outbox (Messenger agent), audit, rollback
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

### How actions are decided

The Strategist picks 1–4 actions from a fixed catalogue (`decision/catalogue.py`)
and cites the policy rules it relied on (`policies/*.md`). Proposals naming an
unknown action, a parameter out of range or a rule that doesn't exist are sent
back to it. Then plain code takes over (`decision/score.py`):

- **Expected value** = (P(recovered with the action) − P(recovered without)) ×
  value at risk − expected cost. The probabilities are hand-set starting
  assumptions in `decision/priors.py`; Phase 6 replaces them with measured rates.
  The model never sets them.
- **Tier:** `auto` (harmless if wrong, or undoable, and within every limit),
  `approval`, or `human_only`. The most important policy limits are written in
  code as well — refunds over 300 EGP always need a person, discounts above 10%
  or 500 EGP need approval — so no prompt or policy edit can loosen them. Every
  tier comes with its reasons and the rule each one enforces.

- **Escalation steps** (`decision/escalation.py`): some actions only make sense
  after a cheaper one has failed. For an unconfirmed order the phone call waits
  until a reminder has gone unanswered for 24 hours, and cancellation until the
  call has failed (COD-4, COD-6). Actions that are ready now rank first, and
  only a ready action can be recommended; later steps are kept as the plan's
  next moves. On paper the call is worth more than the reminder, but the
  reminder costs almost nothing and often solves the problem alone.

The decision engine imports nothing from the model, database or store
(`lint-imports` enforces it), and CI requires 100% branch coverage for it.

### How actions are carried out

`revenueops act` takes each planned case's next ready action and scores it
again at that moment:

- **auto** runs at once: a message goes to the outbox, or the store changes
  (dispatch hold, discount code).
- **approval** waits in the queue until someone approves it. Approval scores it
  again and refuses anything that now needs a person, or whose case closed in
  the meantime.
- **human_only** (a phone call, a cancellation) is never done by the service; a
  person does it and reports back with `complete`.

Each proposed action runs at most once (a unique key in the database). Every
execution keeps before/after snapshots, who decided and when, and its result;
every step is also an event on the case and a note in the store's admin panel.
Rollback reverses the store side (releases the hold, voids an unused code) and
cancels unsent messages; return decisions and anything a person did can't be
undone.

Customer messages are drafted by the **Messenger** agent in Egyptian Arabic.
Code chooses the recipient from the store's records and checks the phone
number; the draft must contain its facts (order reference, discount code) and
may not mention risk or refusals (COMM-5). In this version messages stop in the
outbox — nothing is actually sent.

Policy retrieval is just "every policy tagged with this case type". With five
short documents that is exact and complete; a vector store would only add a way
to miss a rule.

## Run it locally

Requirements: [uv](https://docs.astral.sh/uv/), Docker, and StoreForge running
with `INTEGRATION_API_KEY` set in its `.env` and its migrations applied
(`npm run migration:run`; Phase 4 adds the dispatch hold).

```bash
docker compose up -d postgres
cd agent-service
cp .env.example .env      # then fill in STOREFORGE_API_KEY and a model key
uv sync
uv run alembic upgrade head
uv run revenueops detect
uv run revenueops investigate --limit 3
uv run revenueops plan --limit 3
uv run revenueops act
uv run revenueops queue
uv run uvicorn revenueops.main:app --reload --port 8000
```

- `revenueops detect` scans the store and opens, updates or closes cases.
- `revenueops investigate` runs the Investigator on the most urgent open cases
  (`--case <id>` for one).
- `revenueops plan` runs the Strategist on investigated cases and scores its
  proposals.
- `revenueops act` runs or queues each planned case's next action;
  `revenueops queue` lists what waits for a person; `approve`, `reject`,
  `complete` and `rollback` take an execution id and `--by <your name>`.
- The API adds `GET /executions`, `GET /outbox`, and
  `POST /executions/{id}/approve|reject|complete|rollback` (header
  `X-Admin-Key: $ADMIN_API_KEY`).
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
    agents/         LLM clients, tool loop, Investigator, Strategist
    decision/       action catalogue, starting priors, scorer (pure code)
    executor/       handlers per action, the executor, executions and outbox tables
    policies/       the store's rules as markdown
    pipeline.py     detect, then investigate
    cli.py, main.py command line and HTTP API
  migrations/       Alembic
  tests/
docker-compose.yml  local Postgres on port 5433
```
