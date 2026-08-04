# SmartReco — Development Plan

Build a behavioral AI recommendation platform for the SmartReco Build Challenge 2026.
This document is the single source of truth for architecture, data, and milestones.
All LLM/AI calls (chat **and** embeddings) go through **Mesh API** (OpenAI-compatible gateway).

---

## 1. Decisions (locked)

| Area            | Choice                                                                  |
|-----------------|-------------------------------------------------------------------------|
| Backend         | FastAPI (Python 3.11+)                                                  |
| Frontend        | Jinja2 server-rendered templates + vanilla JS tracking client            |
| Main DB         | PostgreSQL (SQLAlchemy + psycopg) — no SQLite anywhere          |
| Vector DB       | Pinecone (serverless index)                                             |
| LLM/AI access   | Mesh API — `https://api.meshapi.ai/v1`, key `rsk_...` (in `.env`)        |
| Agent framework | LangGraph (explicit reasoning workflow)                                 |
| Scheduling      | APScheduler (in-process background jobs)                                |
| Observability   | Internal tracing layer (agent run log + structured logs); optional LangSmith when key present |
| Auth            | Email/password, bcrypt, DB-backed server session cookies                |

---

## 2. Architecture overview

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

Every user request is lightweight (cache hit) or triggers a *scheduled/debounced*
agent run. No LLM call happens on a bare page view.

---

## 3. Database schema

Model layer is SQLAlchemy (declarative). PostgreSQL is the only database — production
and tests. Dev/test DBs are created by `docker compose up -d db` (main DB `smartreco`,
test DB `smartreco_test` via the init script). JSON columns use `JSONB`.

### `users`
| column      | type        | notes                          |
|-------------|-------------|--------------------------------|
| id          | int PK      |                                |
| email       | text UNIQUE |                                |
| password_hash | text     | bcrypt                         |
| full_name   | text        |                                |
| role        | text        | `user` \| `admin`              |
| created_at  | datetime    |                                |

### `sessions`
| column      | type        | notes                          |
|-------------|-------------|--------------------------------|
| id          | int PK      |                                |
| user_id     | int FK NULL | NULL = anonymous browsing      |
| session_key | text UNIQUE | random, in browser cookie      |
| created_at  | datetime    |                                |
| expires_at  | datetime    |                                |

### `products`
| column      | type        | notes                          |
|-------------|-------------|--------------------------------|
| id          | int PK      |                                |
| title       | text        |                                |
| slug        | text UNIQUE |                                |
| description | text        |                                |
| category    | text        |                                |
| tags        | text JSON   | `["agentic-ai","rag"]`         |
| price       | numeric     |                                |
| level       | text NULL   | beginner/intermediate/advanced |
| image_url   | text NULL   |                                |
| is_active   | bool        | soft delete                    |
| created_at / updated_at | datetime |                       |

### `user_events`
| column      | type        | notes                          |
|-------------|-------------|--------------------------------|
| id          | int PK      |                                |
| user_id     | int FK NULL | set when logged in             |
| session_id  | int FK      | anonymous tracking key         |
| event_type  | text        | `page_view` `product_view` `search` `category_click` `product_click` `add_to_cart` `purchase` `time_spent` |
| entity_type | text NULL   | `product` `category` `query` `page` |
| entity_id   | text NULL   | product id / category / query string |
| payload     | text JSON   | extra signals (duration, source) |
| occurred_at | datetime indexed |                          |

### `recommendations`
| column        | type        | notes                          |
|---------------|-------------|--------------------------------|
| id            | int PK      |                                |
| user_id       | int FK      |                                |
| narrative     | text        | persuasive story               |
| summary       | text        | short headline                 |
| trigger_reason| text        | what activity caused the run   |
| source        | text        | `auto` \| `daily_digest` \| `manual` |
| created_at    | datetime    |                                |
| valid_until   | datetime    | cache validity window          |

### `recommendation_items`
| column           | type   | notes                          |
|------------------|--------|--------------------------------|
| id               | int PK |                                |
| recommendation_id | int FK |                              |
| product_id       | int FK |                                |
| rank             | int    | 1-based order                  |
| score            | real   | retrieval/rerank score         |
| rationale        | text   | why this product fits (LLM)    |

### `agent_runs`  (observability)
| column     | type        | notes                          |
|------------|-------------|--------------------------------|
| id         | int PK      |                                |
| user_id    | int FK      |                                |
| trace_id   | text        | correlated log id              |
| trigger    | text        | event_threshold / digest / manual |
| steps        | text JSON   | analyze / decide / retrieve / evaluate / refine / generate / store |
| llm_calls  | int         |                                |
| total_tokens| int        |                                |
| duration_ms| int         |                                |
| error      | text NULL   |                                |
| created_at | datetime    |                                |

### `email_digests`
| column     | type        | notes                          |
|------------|-------------|--------------------------------|
| id         | int PK      |                                |
| user_id    | int FK      |                                |
| subject    | text        |                                |
| body       | text        | generated email body           |
| status     | text        | queued / sent / failed         |
| sent_at    | datetime NULL |                             |

---

## 4. Pinecone integration & dual-write

### Abstraction
`app/vector_store.py` exposes a `VectorStore` protocol so Pinecone can be swapped
or mocked in tests:

```python
class VectorStore(Protocol):
    def upsert_product(self, product) -> None: ...
    def delete_product(self, product_id) -> None: ...
    def search(self, query_vec, filters, top_k) -> list[ScoreItem]: ...
    def count(self) -> int: ...
```

- Index name from `.env` (`PINECONE_INDEX`). Dimension fixed to the embedding model's
  output (e.g. `openai/text-embedding-3-small` → 1536).
- IDs: `product:{id}`. Metadata stored on the vector: `title`, `category`, `price`,
  `tags`, `level`, `is_active`.

### Dual-write sync rules
- **Create / Update product** → write SQL row → embed text via Mesh → `upsert_product`.
- **Delete / soft-delete** → set `is_active=0` in SQL → `delete_product` from Pinecone.
- **Backfill** → CLI command `python -m app.cli resync_vectors` re-embeds all active
  products (recovery + initial seed).
- **Guarantee:** the product service runs SQL write and vector write in one flow; a
  failure on the vector side is logged to `agent_runs`-style error log and retried by
  the sync job. Catalog is always the source of truth; Pinecone is derived.

### Embeddings
- Use Mesh API `/embeddings` (OpenAI-compatible). Model name configurable in `.env`
  (`EMBEDDING_MODEL`), default `openai/text-embedding-3-small`.
- Embedding text = `title + category + tags + description` (concatenated, length-capped).

### Retrieval robustness
- Primary path is Pinecone semantic retrieval. If Mesh embeddings are unavailable or
  rate-limited, retrieval degrades to a catalog-grounded keyword search (PostgreSQL
  full-text search / `tsvector`) so the agent still returns real, grounded products.
  This resilience path is clearly logged in the agent run trace — it is a fallback,
  never the default.

---

## 5. Behavioral tracking (core focus)

### Frontend tracker (`static/js/tracker.js`)
- **Event types captured:** page_view, product_view, category_click, product_click,
  search, add_to_cart, time_spent.
- **Batching:** events buffered in memory; flush every `BATCH_INTERVAL_MS` (5s) or when
  buffer reaches `BATCH_SIZE` (20). Non-blocking `fetch` with `keepalive: true`; on
  `pagehide`/`visibilitychange` use `navigator.sendBeacon`.
- **Throttling:** high-frequency signals (e.g. scroll depth / duration ticks) coalesced
  client-side to at most one per 10s per signal type.
- **Failure tolerance:** tracker never throws; failures silently dropped and retried on
  next flush.
- **Session id:** from a first-party cookie issued by the backend (anonymous until login;
  merged to `user_id` after login via the session row).

### Backend ingest
- `POST /api/events/batch` accepts a JSON array, validates event types/entity refs,
  writes rows in one transaction, returns `{"status":"ok"}` immediately.
- Payload size capped; malformed items are dropped per-item, not the whole batch.

---

## 6. Agent engine (the heart)

### Trigger policy (efficiency — no LLM per click)
Agent runs when ANY of:
1. **Event threshold:** ≥ 3 meaningful events (`product_view`/`search`/`product_click`/
   `add_to_cart`) since last run.
2. **Cooldown elapsed:** last run older than `MIN_RECO_RUN_INTERVAL` (default 30 min)
   AND ≥ 1 new meaningful event.
3. **Manual refresh:** user clicks "refresh recommendations".
4. **Daily digest:** scheduler time (see §8).

Serving: always serve the latest stored recommendation within `valid_until`; only run
the agent when the trigger fires. Result cached in DB.

### LangGraph workflow (`app/agent/graph.py`)
Nodes (each a LangGraph node, all LLM calls via Mesh). Mapped 1:1 to the bonus spec:
**analyze → decide → retrieve → evaluate → refine → generate → store**.

1. **analyze**
   Input: recent events (last N / since last run) + existing interests.
   Output: structured `InterestProfile` — top themes (with keywords), engagement level,
   urgency signals (cart intent, repeat visits), search intents.
2. **decide_retrieve**
   Decides whether to retrieve at all: insufficient signal (< `MIN_EVENTS`), cached
   recommendation still valid, or no relevant activity → short-circuit (reuse/skip).
   Routes the graph conditionally — the "decide when to retrieve" node.
3. **retrieve**
   Build 1–3 queries from the profile → embed via Mesh → `Pinecone.search` with metadata
   filters (category, level, price cap) → top-k candidates per query → dedupe & merge.
4. **evaluate** *(retrieval quality + rerank)*
   Score candidate quality: semantic similarity + profile-keyword overlap + recency +
   engagement weight (re-visited products rank higher). Compute a quality score; if the
   top result is below threshold → route to **refine**.
5. **refine** *(retrieval polish)*
   Refine/expand the query (rewrite, add synonyms from profile keywords, loosen metadata
   filters), re-embed and re-retrieve. Max 2 loops, then proceed regardless. Refinement
   recorded in the trace.
6. **generate**
   LLM prompt (via Mesh) grounded **only** in the retrieved candidates and the user
   profile. Produces: a personalized headline; a persuasive narrative that reflects the
   user's actual journey (their searches, topics, time spent) with benefit framing and a
   clear call to action — never generic "popular products" copy; and ranked picks with a
   1-line rationale each.
7. **store**
   Persist `recommendations` + `recommendation_items` + `agent_runs` trace; invalidate
   old recommendations (set `valid_until` in the past).

### Grounding guarantees
- `generate` output is validated: every recommended `product_id` must exist in the
  `evaluate` step's candidate list. Anything else is rejected and regenerated (max 2 retries).
- Catalog is the only product source (no hallucinated products).

---

## 7. API surface (FastAPI)

| Method | Path                        | Auth      | Purpose                              |
|--------|-----------------------------|-----------|--------------------------------------|
| POST   | `/auth/register`            | public    | create account                       |
| POST   | `/auth/login`               | public    | login, set session cookie            |
| POST   | `/auth/logout`              | session   | logout                               |
| GET    | `/`                         | session*  | catalog home                         |
| GET    | `/products/{slug}`          | session*  | product detail (tracks view)         |
| GET    | `/search?q=`                | session*  | search results (tracks query)        |
| GET    | `/recommendations`          | user      | personalized recommendations page    |
| POST   | `/recommendations/refresh`  | user      | force agent run                      |
| GET    | `/admin/products`           | admin     | product list                         |
| GET    | `/admin/products/new`       | admin     | create form                          |
| POST   | `/admin/products`           | admin     | create (dual-write)                  |
| GET    | `/admin/products/{id}/edit` | admin     | edit form                            |
| POST   | `/admin/products/{id}`      | admin     | update (dual-write)                  |
| POST   | `/admin/products/{id}/delete` | admin    | soft delete + vector delete          |
| POST   | `/api/events/batch`         | session*  | ingest batched events                |
| GET    | `/api/recommendations/latest` | user    | JSON for async refresh               |
| POST   | `/api/digest/test`          | admin     | manual digest trigger (dev/debug)    |

`session*` = requires an anonymous session id cookie (issued automatically).

---

## 8. Scheduled proactive delivery (bonus)

- **APScheduler** started with the FastAPI app (lifespan), one daily job.
- At `DIGEST_TIME` (default 09:00, timezone-aware):
  1. Select users with meaningful events in the last 24h and no digest sent today.
  2. For each: run the agent (trigger `daily_digest`) or reuse cached recommendation.
  3. Render a persuasive email (subject + narrative + top-3 products).
  4. Send via SMTP (`.env`: `SMTP_HOST/PORT/USER/PASSWORD`); record in `email_digests`.
- Idempotent: one digest per user per day (index/guard in `email_digests`).
- SMTP is optional at runtime; sending failure is logged, not fatal. A local
  `maildev`/console logger fallback keeps dev/test working without credentials.

---

## 9. Observability (bonus)

- **Primary:** `agent_runs` table captures every run — steps, llm_calls, tokens,
  duration, errors. Admin `/admin/observability` page renders recent runs.
- **Logs:** structured JSON logs per agent run with `trace_id` (one per run, propagated
  to every log line of that run).
- **Optional LangSmith:** if `LANGSMITH_API_KEY` is present in `.env`, the LangGraph
  run is also traced via the LangGraph/LangSmith integration. Feature-gated; never
  required.

---

## 10. Auth & security

- bcrypt password hashing (`passlib`/`bcrypt`).
- DB-backed session cookie (HttpOnly, SameSite=Lax, secure flag in prod).
- Anonymous sessions get a cookie; on login the session row is linked to `user_id`
  (no event loss).
- All secrets in `.env`, gitignored. No hardcoded keys.
- Admin routes protected by role check; 403 otherwise.

---

## 11. Project layout

```
SmartReco/
├── app/
│   ├── main.py                 # FastAPI app, lifespan (DB init, scheduler)
│   ├── config.py               # .env loading, settings
│   ├── database.py             # SQLAlchemy engine/session
│   ├── models.py               # ORM models
│   ├── schemas.py              # Pydantic schemas
│   ├── auth.py                 # register/login/logout, sessions, deps
│   ├── router/                 # routers: pages.py, api.py, admin.py, auth.py
│   ├── services/
│   │   ├── products.py         # CRUD + dual-write orchestration
│   │   ├── events.py           # batch ingest
│   │   ├── recommendations.py  # trigger policy, cache, serve
│   │   ├── mesh.py             # Mesh client (chat + embeddings), retries
│   │   └── digest.py           # email rendering + send
│   ├── agent/
│   │   ├── graph.py            # LangGraph workflow definition
│   │   ├── nodes.py            # analyze/decide/retrieve/evaluate/refine/generate/store
│   │   ├── prompts.py          # prompt templates
│   │   └── profile.py          # InterestProfile parsing/validation
│   ├── vector_store.py         # Pinecone implementation + mock
│   ├── scheduler.py            # APScheduler jobs
│   ├── observability.py        # trace/log helpers
│   └── cli.py                  # resync_vectors, seed_demo, digest now
├── templates/                  # Jinja2: base, index, product, search,
│                               # recommendations, admin/*, auth/*
├── static/                     # css, js/tracker.js
├── tests/                      # pytest suite
├── requirements.txt
├── .env.example
├── .gitignore
├── docker-compose.yml          # local PostgreSQL
├── README.md
└── .github/workflows/smartreco-checks.yml
```

---

## 12. Efficiency & caching summary (judged criteria)

- No LLM on page view; trigger policy + cooldown (§6).
- Recommendations cached in DB until `valid_until`.
- Events batched client-side + single-transaction server writes.
- Embeddings cached per product (reuse on re-run when product unchanged).
- Prompt budgets: cheap model for analysis, strong model only for final narrative.
- Coalescing/throttling of high-frequency client signals (§5).

---

## 13. Testing strategy

- **pytest** + FastAPI `TestClient`.
- Mock Mesh API (no network/key in CI): `tests/fakes/mesh.py` returns canned
  completions/embeddings. Verify the app never calls Mesh directly (only via service).
- Mock vector store (`InMemoryVectorStore`) for tests; Pinecone path covered by a
  guarded integration test that skips when no `PINECONE_API_KEY`.
- Tests: auth flow, product dual-write sync, event ingest (valid/invalid/batched),
  trigger policy (when agent runs / when it doesn't), agent grounding validation
  (rejects hallucinated product ids), digest idempotency, admin authorization.
- Command: `pytest`.

---

## 14. Submission & CI

- Public GitHub repo = submission. `requirements.txt` includes `fastapi`, an LLM client
  (`openai`) and `langgraph`.
- `.gitignore`: `.env`, `*.db`, `__pycache__/`, `.venv/`, etc.
- Postgres is required: `docker compose up -d db` creates the `smartreco` and
  `smartreco_test` databases (init script). No SQLite fallback exists.
- `.github/workflows/smartreco-checks.yml` downloaded from
  `https://careerapi-production.krishnaik.in/api/ci/hackathons/smartreco-build-challenge-2026/workflow.yml`
- GitHub secrets: `MESH_API_KEY`, `SUBMISSION_TOKEN` (not used at runtime by app).
- README.md: architecture, setup, run instructions, bonus features list.
- Code must compile with zero syntax errors — Python files are checked in CI.

---

## 15. Milestones / roadmap

| # | Milestone                                   | Deliverable                                          |
|---|---------------------------------------------|------------------------------------------------------|
| 1 | Scaffold                                    | FastAPI app, config, DB models, migrations, auth, templates base, session cookie |
| 2 | Product CRUD + dual-write                   | Admin pages, Mesh embeddings, Pinecone upsert/delete, resync CLI |
| 3 | Event tracking                              | tracker.js, ingest API, session merging, throttling/batching |
| 4 | Agent engine                                | LangGraph workflow, Mesh LLM calls, retrieval, grounding validation, agent_runs |
| 5 | Recommendations UI                          | Recommendations page, refresh trigger, narrative display |
| 6 | Scheduling + email                          | APScheduler daily digest, email rendering/sending, idempotency |
| 7 | Observability                               | trace_id logs, admin observability page, optional LangSmith |
| 8 | Polish + tests + CI + README                | Full pytest suite, workflow file, README, seed demo data, .env.example |
| 9 | Submission prep                             | Public repo, secrets, workflow run green, final review |

---

## 16. Risks & mitigations

| Risk | Mitigation |
|------|-----------|
| Pinecone needs network + API key at runtime | `VectorStore` abstraction; mock in tests; CI never calls it; graceful degradation with clear error logs |
| Mesh API availability / rate limits | Central `mesh.py` with retries, timeouts, token budgets, prompt size caps |
| Embedding model availability via Mesh | Model name in `.env`; verify `openai/text-embedding-3-small` works at kickoff |
| Scheduler timezones | Configurable `DIGEST_TIME` + explicit `ZoneInfo` tz |
| Auto-checks require zero syntax errors | Run `python -m compileall app tests` in CI + locally before push |
| No hardcoded secrets | `.env` only; `.env.example` with placeholders |
