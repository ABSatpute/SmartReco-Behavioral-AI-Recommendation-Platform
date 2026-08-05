# SmartReco — Behavioral AI Recommendation Platform

SmartReco watches how a user actually behaves (searches, product views, clicks,
cart intent) and turns that into **personalized, persuasive product
recommendations** — powered by a LangGraph reasoning agent that retrieves from a
Pinecone vector store and generates grounded narratives.

Built for the **SmartReco Build Challenge 2026**. All LLM and embedding calls go
through the **Mesh API** gateway (OpenAI-compatible).

---

## Highlights / bonus features

| Feature | Detail |
|---|---|
| **Agentic reasoning (LangGraph)** | Explicit workflow: `analyze → decide → retrieve → evaluate → refine → generate → store` |
| **Retrieval polish** | Refine loops rewrite queries, loosen filters, and re-retrieve (max 2 loops) before settling |
| **Proactive email digests (APScheduler)** | Daily personalized email for active users, idempotent per user per day |
| **Observability** | `trace_id` propagation, structured JSON logs, `agent_runs` trace, admin page, optional LangSmith tracing |
| **Grounded output** | Hallucinated product ids are rejected and regenerated; catalog is the only source of truth |
| **Efficiency** | No LLM call on a page view — trigger policy, cooldown, and DB-cached recommendations |

---

## Architecture

```
Browser (Jinja2 + JS tracker)
   │  POST /api/events/batch   (batched, beacon on unload)
   ▼
FastAPI app
   ├── Auth/session layer
   ├── Product CRUD ──► PostgreSQL ──► Pinecone (dual-write, kept in sync)
   ├── Event ingest (validates, batches, persists)
   ├── Recommendation service (cache + trigger policy)
   │        │
   │        ▼
   │   Agent engine (LangGraph workflow)  ──► Mesh API (LLM + embeddings)
   │        │
   │        ▼
   │   Pinecone retrieval (RAG, grounded in real catalog)
   │        ▼
   ├── Recommendations stored in DB, served to UI
   └── APScheduler ──► daily digest agent run + email send
        └── Observability: agent_runs trace + JSON logs (optional LangSmith)
```

### Trigger policy (judged criteria)

The agent runs only when **any** of these fire — never on a bare page view:

1. ≥ 3 meaningful events (`product_view`, `search`, `product_click`, `add_to_cart`) since the last run,
2. cooldown elapsed (default 30 min) **and** ≥ 1 new meaningful event,
3. the user clicks "Refresh recommendations",
4. the daily digest scheduler fires.

The latest valid recommendation is always served from the DB cache.

### Agent workflow (`app/agent/`)

LangGraph `StateGraph` with 7 nodes (`graph.py`):

1. **analyze** — build an `InterestProfile` (top themes, engagement, urgency, search intents) from recent events.
2. **decide** — insufficient signal? Short-circuit to `store` (reuse/skip).
3. **retrieve** — build 1–3 queries → embed via Mesh → Pinecone search with metadata filters → dedupe & merge.
4. **evaluate** — score candidates (semantic similarity + profile-keyword overlap + engagement weight); below threshold → refine.
5. **refine** — rewrite/expand queries, re-embed, re-retrieve (max 2 loops).
6. **generate** — LLM writes a personalized headline, persuasive narrative, and ranked picks with rationales — grounded only in retrieved candidates.
7. **store** — persist `recommendations`, `recommendation_items`, and the `agent_runs` trace; invalidate old ones.

### Grounding guarantee

Every recommended `product_id` must come from the evaluated candidate list. Anything
else triggers a regeneration (max 2 retries), then a catalog-grounded fallback.

---

## Tech stack

- **Backend:** FastAPI (Python 3.11+), SQLAlchemy 2, Jinja2
- **Database:** PostgreSQL only (no SQLite anywhere) — JSONB columns, `tsvector`-ready
- **Vector store:** Pinecone (serverless), index `smartreco` (1536-dim, cosine)
- **LLM/embeddings:** Mesh API (`openai/gpt-4o` for narrative, `minimax/m2-her` for analysis, `openai/text-embedding-3-small` for embeddings)
- **Agent:** LangGraph
- **Scheduler:** APScheduler
- **Auth:** bcrypt + DB-backed session cookies (HttpOnly, SameSite=Lax)

---

## Setup

### 1. Prerequisites

- Python 3.11+
- Docker (for local PostgreSQL)
- A Mesh API key (`rsk_...`) — all LLM/embedding calls route through Mesh
- (Optional) a Pinecone API key + a `smartreco` index
- (Optional) a LangSmith API key for tracing

### 2. Start PostgreSQL

```bash
docker compose up -d db
```

This creates both databases (main `smartreco` + test `smartreco_test`).

### 3. Configure environment

```bash
cp .env.example .env
# then fill in MESH_API_KEY (required), PINECONE_API_KEY, SMTP_*, LANGSMITH_API_KEY
```

### 4. Install and run

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows  (Linux/macOS: source .venv/bin/activate)
pip install -r requirements.txt
python -m app.cli seed_demo     # demo products + admin@smartreco.dev / adminpass123
uvicorn app.main:app --reload
```

Open http://localhost:8000. Register an account and browse/search products —
then visit **My Recommendations**.

---

## CLI (`python -m app.cli`)

| Command | Purpose |
|---|---|
| `create_admin --email E --password P` | Create an admin user |
| `seed_demo` | Seed 8 demo products + demo admin |
| `load_amazon --csv PATH [--limit N] [--category S]` | Import Amazon UK 2023 dataset |
| `resync_vectors` | Re-embed all active products into Pinecone (backfill) |
| `check_vectors` | Report vector-store vs SQL product counts |
| `digest` | Run the daily digest now (dev/debug) |

---

## Tests

Tests run against a PostgreSQL database (`smartreco_test`) — configured via
`DATABASE_URL` in `tests/conftest.py`. They mock Mesh API and use an in-memory
vector store, so no network or keys are needed.

```bash
python -m pytest
```

Coverage: auth, product dual-write sync, event ingest, trigger policy, agent
grounding validation, digest idempotency, admin authorization, observability.

---

## Observability

- **`agent_runs`** table records every agent run: steps, LLM calls, tokens, duration, errors.
- **Admin → Observability** page renders recent runs + stats.
- **Structured JSON logs** with a `trace_id` (echoed as the `x-trace-id` response header).
- **LangSmith**: set `LANGSMITH_API_KEY` in `.env` to also export LangGraph traces to LangSmith.

---

## Project layout

```
app/
  main.py               FastAPI app, lifespan (DB init, scheduler, logging)
  config.py             settings from .env
  database.py           SQLAlchemy engine/session
  models.py             ORM models (users, sessions, products, events, recs, agent_runs, digests)
  router/               pages.py, api.py, admin.py, auth.py
  services/             products, events, recommendations, mesh, digest, auth
  agent/                graph.py, nodes.py, prompts.py, profile.py
  vector_store.py       Pinecone implementation + in-memory mock
  scheduler.py          APScheduler daily digest job
  observability.py      trace_id + JSON logging + LangSmith gate
  cli.py                admin/seed/amazon/resync/digest commands
templates/              Jinja2 pages + email template
static/                 CSS + js/tracker.js (batched beacon tracking)
tests/                  pytest suite
```

---

## Environment variables

See [`.env.example`](.env.example) for the full list with defaults. Mandatory:
`MESH_API_KEY`, `DATABASE_URL`. Everything else has a sensible default or is optional.
**Never commit `.env`.**

---

## CI / submission

The repo includes `.github/workflows/smartreco-checks.yml`, the official
Build Challenge 2026 check workflow. It runs on every push and reports results to
the challenge server using an OIDC token plus the `SUBMISSION_TOKEN` and
`MESH_API_KEY` GitHub secrets.
