# SmartReco — Behavioral AI Recommendation Platform

SmartReco watches how a user actually behaves — searches, product views, clicks,
cart intent, time spent — and turns that into **personalized, persuasive product
recommendations** powered by a **LangGraph reasoning agent** that retrieves from a
**Pinecone** vector store and generates **catalog-grounded** narratives.

Built for the **SmartReco Build Challenge 2026**. All LLM and embedding calls go
through the **Mesh API** gateway (OpenAI-compatible). Live: <https://smartreco-sogc.onrender.com>

---

## Highlights / bonus features

| Feature | Detail |
|---|---|
| **Agentic reasoning (LangGraph)** | Explicit 7-node workflow: `analyze → decide → retrieve → evaluate → refine → generate → store` |
| **Retrieval polish** | Refine loops rewrite queries, loosen filters, and re-retrieve (max 2 loops) before settling |
| **Proactive delivery** | Daily personalized digest via **email and Telegram** (APScheduler in-process + Render Cron job), idempotent per user per day |
| **Observability** | `trace_id` propagation, structured JSON logs, `agent_runs` trace, admin observability page with stats/charts + per-run detail, optional LangSmith tracing |
| **Grounded output** | Hallucinated product ids are rejected and regenerated; the catalog is the only source of truth |
| **Efficiency** | No LLM call on a page view — trigger policy, cooldown, and DB-cached recommendations |
| **Full storefront** | Amazon-pattern catalog, search, cart + checkout, guest-cart merge, account/profile pages |
| **Live activity feed** | Admin page streaming real-time behavioral events over SSE |
| **Admin user management** | Create / edit / delete users with role assignment |

---

## Architecture

```
Browser (Jinja2 + JS tracker — batched, throttled, beacon-on-unload)
   │  POST /api/events/batch
   ▼
FastAPI app
   ├── Auth/session layer (bcrypt + DB session cookie)
   ├── Catalog + admin users ──► PostgreSQL ──► Pinecone (dual-write, in sync)
   ├── Event ingest (validates, batches, persists) ──► Live Activity SSE
   ├── Cart / checkout, browse sessions
   ├── Recommendation service (cache + trigger policy)
   │        │
   │        ▼
   │   Agent engine (LangGraph)  ──► Mesh API (LLM + embeddings)
   │        │                        ▼
   │        └────────── Pinecone retrieval (RAG, grounded in real catalog)
   │        ▼
   ├── Recommendations stored in DB, served to UI
   ├── APScheduler ──► daily digest agent run ──► email + Telegram
   └── Render Cron (/cron/digest, /cron/sessions) for exact-time delivery
        └── Observability: agent_runs trace + JSON logs (optional LangSmith)
```

## How the agent works

### When does the agent run? (trigger policy — judged efficiency criterion)

The agent is deliberately lazy: a bare page view never costs an LLM call. The user
always gets the **latest cached recommendation** (`valid_until`) when it's still fresh.
A run is only triggered when **any** of these fire:

1. **Signal threshold** — ≥ `min_events_threshold` (default 3) *meaningful* events since
   the last run: `product_view`, `search`, `product_click`, or `add_to_cart`.
2. **Cooldown + activity** — the cooldown has elapsed (default 30 min) **and** there's at
   least 1 new meaningful event since the last run.
3. **Manual refresh** — the user clicks "Refresh recommendations".
4. **Scheduled / cron** — the daily digest scheduler or a cron endpoint fires
   (see *When are notifications sent?* below).

A background worker re-checks the same policy before running, so concurrent page views
can't double-spend tokens (`_RUNNING_USERS` guard).

### What does the agent do? (workflow, `app/agent/`)

LangGraph `StateGraph` with 7 nodes (`graph.py`):

1. **analyze** — build an `InterestProfile` (top themes, engagement, urgency, search intents) from recent events.
2. **decide** — insufficient signal? Short-circuit to `store` (reuse/skip), no LLM generation.
3. **retrieve** — build 1–3 queries → embed via Mesh → Pinecone search with metadata filters → dedupe & merge.
4. **evaluate** — score candidates (semantic similarity + profile-keyword overlap + engagement weight); below threshold → refine.
5. **refine** — rewrite/expand queries, re-embed, re-retrieve (max 2 loops).
6. **generate** — LLM writes a personalized headline, persuasive narrative, and ranked picks with rationales — grounded only in retrieved candidates.
7. **store** — persist `recommendations`, `recommendation_items`, and the `agent_runs` trace; invalidate old ones.

Skips are **not** failures: a run that deliberately declines to recommend (insufficient
signal) is marked `skipped`, excluded from failure counts, and surfaced separately in
both the dashboard and observability.

### When are notifications sent? (proactive delivery)

Notifications are **push-only follow-ups** — the user doesn't need to be browsing.
Messages send over **email and/or Telegram**, gated by the `notification_channels_list`
setting. There are two delivery jobs (see `app/scheduler.py` + `app/router/cron.py`):

**1. Daily digest — at most once per user per day**
- Runs on a 30-minute in-process cadence (APScheduler) plus a best-effort catch-up at
  boot — so any wake-up of the free-tier (spin-down) instance delivers promptly.
  A per-user per-day guard (`_already_digested_today`) keeps it to **one digest/day**.
  For exact-time delivery, a **Render Cron Job** hits `/cron/digest` (secret-protected).
- Only users who have a fresh recommendation to share are emailed; a user with no
  recommendable activity gets nothing and is counted as *skipped / no recommendation*.
- Each digest runs the agent (`force=True`, source `daily_digest`) unless a valid
  recommendation is already cached.

**2. Session follow-ups — 1h / 6h / 12h after a browsing session ends**
- A browsing session ends after `session_gap_minutes` of inactivity.
- At each follow-up slot (`session_slots_hours`, default `1,6,12`), a sweep
  (`run_session_digests` on a 15-min cadence, or `/cron/sessions`) sends an
  abandoned-intent follow-up with the products from that session.
- Only sessions with meaningful activity participate; the recommendation built for the
  session is **reused** for later slots, so it's generated once, not per slot.
- Follow-ups are idempotent per session-and-slot (`session_digests` table).

You can trigger either manually for testing: `python -m app.cli run_digest_now`.

### Grounding guarantee

Every recommended `product_id` must come from the evaluated candidate list. Anything
else triggers a regeneration (max 2 retries), then a catalog-grounded fallback.

---

## Tech stack

- **Backend:** FastAPI (Python 3.11+), SQLAlchemy 2, Jinja2
- **Database:** PostgreSQL only (no SQLite anywhere) — JSONB columns
- **Vector store:** Pinecone (serverless), index `smartreco` (1536-dim, cosine)
- **LLM/embeddings:** Mesh API (`openai/gpt-4o` narrative, `minimax/m2-her` analysis, `openai/text-embedding-3-small` embeddings)
- **Agent:** LangGraph
- **Scheduler:** APScheduler (+ Render Cron endpoints)
- **Notifications:** SMTP email + Telegram Bot API
- **Auth:** bcrypt + DB-backed session cookies (HttpOnly, SameSite=Lax)
- **Live feed:** Server-Sent Events (admin)

---

## Setup

### 1. Prerequisites

- Python 3.11+
- Docker (local PostgreSQL)
- A Mesh API key (`rsk_...`) — all LLM/embedding calls route through Mesh
- (Optional) a Pinecone API key + a `smartreco` index
- (Optional) LangSmith, SMTP, and/or Telegram credentials

### 2. Start PostgreSQL

```bash
docker compose up -d db
```

Creates both `smartreco` and `smartreco_test`.

### 3. Configure environment

```bash
cp .env.example .env
# fill in MESH_API_KEY (required), PINECONE_API_KEY, SMTP_* / TELEGRAM_BOT_TOKEN, LANGSMITH_API_KEY
```

### 4. Install and run

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows  (Linux/macOS: source .venv/bin/activate)
pip install -r requirements.txt
python -m app.cli seed_demo     # demo products + admin@smartreco.dev / adminpass123
uvicorn app.main:app --reload
```

Open http://localhost:8000. Register an account and browse/search/products/cart —
then visit **My Recommendations** (or the admin Observability page).

---

## CLI (`python -m app.cli`)

| Command | Purpose |
|---|---|
| `create_admin --email E --password P` | Create an admin user |
| `seed_demo` | Seed demo products + demo admin |
| `load_amazon --csv PATH [--limit N] [--category S]` | Import Amazon UK 2023 dataset |
| `resync_vectors` | Re-embed all active products into Pinecone (backfill) |
| `check_vectors` | Report vector-store vs SQL product counts |
| `run_digest_now` | Run the daily digest now (dev/debug) |
| `set_telegram --email E --chat-id ID` | Link a Telegram chat for a user |
| `telegram_chats` | List registered Telegram chats |
| `test_email --to E [--message M]` | Send a test email |

---

## Tests

Tests run against PostgreSQL (`smartreco_test`), mock Mesh API, and use an in-memory
vector store — no network or keys needed.

```bash
python -m pytest
```

**90 tests** across 11 files covering: auth, product dual-write sync, event ingest,
trigger policy, agent grounding (rejects hallucinated ids), digest idempotency
(email + telegram), cart, browse sessions, live activity/SSE, admin authorization,
and observability. A fixture terminates idle-in-transaction connections before each
DDL reset so the whole suite runs without teardown deadlocks.

---

## Observability

- **`agent_runs`** table records every agent run: steps, LLM calls, tokens, duration, errors.
- **Admin → Observability** renders KPIs, ok/skipped/failed charts, trigger & failure
  breakdowns, cost estimates, filters, and a per-run **trace detail** page.
- **Admin → Dashboard** shows the same skipped/failed reclassification plus a live chart.
- **Admin → Live Activity** streams events over SSE in real time.
- **Structured JSON logs** with a `trace_id` (echoed as the `x-trace-id` header).
- **LangSmith**: set `LANGSMITH_API_KEY` to also export LangGraph traces.

---

## Project layout

```
app/
  main.py               FastAPI app, lifespan (DB init, scheduler, logging, middleware)
  config.py             settings from .env
  database.py           SQLAlchemy engine/session
  models.py             ORM: users, sessions, products, cart_items, browse_sessions,
                        session_digests, user_events, recommendations,
                        recommendation_items, agent_runs, email_digests
  deps.py               auth deps + admin guard (require_admin)
  schemas.py            Pydantic schemas
  flash.py / utils.py / observability.py / rate_limit.py / templating.py
  router/               pages.py, api.py, admin.py, auth.py, cron.py
  services/             products, events, recommendations, mesh, digest, cart,
                        browse_sessions, auth, observability_metrics
  agent/                graph.py (7 nodes), nodes.py, prompts.py, profile.py
  vector_store.py       Pinecone impl + in-memory/null mocks
  scheduler.py          APScheduler digest jobs
  cli.py                admin/seed/amazon/resync/digest/telegram/email commands
templates/              Jinja2 pages (storefront, auth, account, cart, admin/*, emails/*)
static/                 css + js/tracker.js (batched beacon tracking) + admin charts
tests/                  pytest suite (11 files, 90 tests)
data/                   sampled Amazon catalog (eda/sample_amazon.py)
eda/                    Amazon EDA notebook + summary
```

---

## Environment variables

See [`.env.example`](.env.example) for the full list with defaults. Mandatory:
`MESH_API_KEY`, `DATABASE_URL`. Others are optional or have sensible defaults.

**Never commit `.env`.**

---

## Deployment & CI / submission

- `render.yaml` defines the Postgres DB + web service and auto-deploys from `main`
  to <https://smartreco-sogc.onrender.com>.
- `.github/workflows/smartreco-checks.yml` — the official Build Challenge 2026 check
  workflow (compile + dependency checks, plus the advisory README/.env/.gitignore checks).
- GitHub secrets used: `MESH_API_KEY`, `SUBMISSION_TOKEN`, plus Render secrets for
  Pinecone/SMTP/Telegram.
