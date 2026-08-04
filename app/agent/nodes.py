"""LangGraph nodes: analyze -> decide -> retrieve -> evaluate -> refine -> generate -> store.

All LLM/AI calls go through app.services.mesh (Mesh API). Each node is a pure
function of the AgentState and returns the fields it changes.
"""
import logging
import time

from app.agent.profile import heuristic_profile, parse_profile
from app.agent.prompts import (
    ANALYZE_SYSTEM,
    FALLBACK_RATIONALE,
    GENERATE_SYSTEM,
    analyze_user_prompt,
    generate_user_prompt,
)
from app.config import settings
from app.database import SessionLocal
from app.models import AgentRun, Product, Recommendation, RecommendationItem
from app.services import mesh
from app.utils import utcnow_naive, utcnow_naive_delta
from app.vector_store import get_vector_store

logger = logging.getLogger(__name__)

SEEN_PRODUCT_BONUS = 0.15
QUALITY_THRESHOLD = 0.35


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _append_step(state: dict, node: str, note: str) -> list:
    steps = list(state.get("steps", []))
    steps.append({"node": node, "note": note})
    return steps


def _product_snapshot(product: Product) -> dict:
    return {
        "id": product.id,
        "title": product.title,
        "category": product.category,
        "price": float(product.price),
        "stars": float(product.stars) if product.stars is not None else None,
        "reviews": int(product.reviews) if product.reviews is not None else 0,
        "best_seller": bool(product.is_best_seller),
        "slug": product.slug,
        "image_url": product.image_url,
    }


def _seen_product_ids(events: list[dict]) -> set[int]:
    ids: set[int] = set()
    for event in events:
        if event.get("entity_type") == "product":
            try:
                ids.add(int(event.get("entity_id")))
            except (TypeError, ValueError):
                continue
    return ids


def _query_vector_fallback(db, query: str) -> list[dict]:
    """Catalog-grounded keyword fallback when embeddings/vector search fail."""
    from app.services import products as product_service

    products = product_service.search_products(db, query)
    results = []
    for rank, product in enumerate(products):
        snapshot = _product_snapshot(product)
        snapshot["score"] = max(0.3 - rank * 0.05, 0.0)
        results.append(snapshot)
    return results


# --------------------------------------------------------------------------- #
# nodes
# --------------------------------------------------------------------------- #
def analyze(state: dict) -> dict:
    events = state.get("events", [])
    summary = state.get("event_summary", "")
    llm_calls = state.get("llm_calls", 0)
    total_tokens = state.get("total_tokens", 0)
    fallback_used = state.get("fallback_used", False)

    try:
        content, usage = mesh.chat_meta(
            [
                {"role": "system", "content": ANALYZE_SYSTEM},
                {
                    "role": "user",
                    "content": analyze_user_prompt(summary, len(events)),
                },
            ],
            model=settings.analysis_model,
        )
        profile = parse_profile(content).to_dict()
        llm_calls += 1
        total_tokens += usage
        note = "analyzed events via LLM"
    except mesh.MeshError as exc:
        logger.warning("Analysis LLM failed, using heuristic profile: %s", exc)
        profile = heuristic_profile(events, summary).to_dict()
        fallback_used = True
        note = "analyzed events via heuristic fallback"

    return {
        "profile": profile,
        "llm_calls": llm_calls,
        "total_tokens": total_tokens,
        "fallback_used": fallback_used,
        "steps": _append_step(state, "analyze", note),
    }


def decide(state: dict) -> dict:
    profile = state.get("profile") or {}
    themes = profile.get("themes", [])
    keywords = profile.get("keywords", [])
    intents = profile.get("search_intents", [])
    signal = len(themes) + len(keywords) + len(intents)

    queries = []
    for q in intents + themes:
        q = q.strip()
        if q and len(q) >= 2 and q not in queries:
            queries.append(q[:120])
        if len(queries) >= 3:
            break

    if not queries or signal < settings.min_events_threshold:
        return {
            "skip_reason": "insufficient behavioral signal",
            "steps": _append_step(state, "decide", f"skipped (signal={signal}, queries={len(queries)})"),
        }
    return {
        "skip_reason": None,
        "queries": queries,
        "steps": _append_step(state, "decide", f"queries: {queries}"),
    }


def retrieve(state: dict) -> dict:
    queries = state.get("queries", [])
    fallback_used = state.get("fallback_used", False)
    seen = _seen_product_ids(state.get("events", []))
    merged: dict[int, dict] = {}

    db = SessionLocal()
    try:
        for query in queries:
            try:
                vector = mesh.embed_one(query)
                hits = get_vector_store().query(
                    vector, filters={"is_active": True}, top_k=5
                )
                for hit in hits:
                    product_id = _id_from_vector(hit.id)
                    if product_id is None:
                        continue
                    existing = merged.get(product_id)
                    if existing is None or hit.score > existing["score"]:
                        merged[product_id] = {
                            "score": float(hit.score),
                            "seen": product_id in seen,
                        }
            except mesh.MeshError as exc:
                logger.warning("Embedding/retrieval failed, using keyword fallback: %s", exc)
                fallback_used = True
                for snapshot in _query_vector_fallback(db, query):
                    pid = snapshot["id"]
                    existing = merged.get(pid)
                    if existing is None or snapshot["score"] > existing["score"]:
                        merged[pid] = {
                            "score": snapshot["score"],
                            "seen": pid in seen,
                        }

        products = (
            db.query(Product)
            .filter(Product.id.in_(list(merged.keys())), Product.is_active.is_(True))
            .all()
        )
        candidates = []
        for product in products:
            candidate = _product_snapshot(product)
            candidate["score"] = merged[product.id]["score"]
            candidate["seen"] = merged[product.id]["seen"]
            candidates.append(candidate)
        candidates.sort(key=lambda c: c["score"], reverse=True)
    finally:
        db.close()

    return {
        "candidates": candidates,
        "fallback_used": fallback_used,
        "steps": _append_step(state, "retrieve", f"{len(candidates)} candidates from {len(queries)} queries"),
    }


def evaluate(state: dict) -> dict:
    profile = state.get("profile") or {}
    keywords = profile.get("keywords", [])
    candidates = list(state.get("candidates", []))

    for c in candidates:
        text = f"{c['title']} {c['category']}"
        overlap = sum(1 for kw in keywords if kw.lower() in text.lower())
        composite = c["score"] + 0.05 * overlap
        if c.get("seen"):
            composite += SEEN_PRODUCT_BONUS
        popularity = min(c.get("reviews", 0) / 1000, 1) * 0.03 + min(
            c.get("bought", 0) / 100, 1
        ) * 0.02
        composite += popularity
        c["overlap"] = overlap
        c["composite"] = round(composite, 4)

    candidates.sort(key=lambda c: c["composite"], reverse=True)
    candidates = candidates[:5]

    attempts = state.get("attempts", 0)
    best = candidates[0] if candidates else None
    need_refine = (
        best is None
        or (best["composite"] < QUALITY_THRESHOLD and best.get("overlap", 0) == 0)
    ) and attempts < settings.agent_max_refine_loops

    action = "refine" if need_refine else "generate"
    note = f"{len(candidates)} ranked; best={best['title'] if best else 'none'} composite={best['composite'] if best else 0}; -> {action}"
    return {"candidates": candidates, "action": action, "steps": _append_step(state, "evaluate", note)}


def refine(state: dict) -> dict:
    attempts = state.get("attempts", 0) + 1
    keywords = (state.get("profile") or {}).get("keywords", [])
    queries = list(state.get("queries", []))

    expanded = []
    for q in queries:
        q2 = q
        added = 0
        for kw in keywords:
            if kw.lower() not in q2.lower() and added < 2:
                q2 = f"{q2} {kw}"
                added += 1
        if q2 not in expanded:
            expanded.append(q2)
        if len(expanded) >= 2:
            break
    if not expanded:
        expanded = queries

    return {
        "attempts": attempts,
        "queries": expanded,
        "steps": _append_step(state, "refine", f"attempt {attempts}: queries={expanded}"),
    }


def generate(state: dict) -> dict:
    candidates = list(state.get("candidates", []))
    llm_calls = state.get("llm_calls", 0)
    total_tokens = state.get("total_tokens", 0)

    if not candidates:
        return {
            "result": None,
            "steps": _append_step(state, "generate", "no candidates to present"),
        }

    profile = state.get("profile") or {}
    candidate_ids = {c["id"] for c in candidates}
    messages = [
        {"role": "system", "content": GENERATE_SYSTEM},
        {"role": "user", "content": generate_user_prompt(profile, candidates)},
    ]

    result = None
    attempts = 0
    max_retries = settings.agent_max_generate_retries
    while result is None and attempts <= max_retries:
        try:
            content, usage = mesh.chat_meta(messages, model=settings.llm_model)
            llm_calls += 1
            total_tokens += usage
            parsed = _extract_generation(content)
            picks = _validate_picks(parsed, candidate_ids)
            if picks is not None:
                result = {
                    "summary": str(parsed.get("summary", "Top picks for you"))[:500],
                    "narrative": str(parsed.get("narrative", ""))[:4000],
                    "picks": picks,
                }
        except mesh.MeshError as exc:
            logger.warning("Generation LLM failed: %s", exc)
            llm_calls += 1
        attempts += 1

    if result is None:
        themes = profile.get("themes") or ["the topics you explored"]
        theme = themes[0]
        result = {
            "summary": "Top picks for you",
            "narrative": (
                f"Based on your interest in {theme}, here are the best matches we found "
                f"in the catalog. Take a look before your next session ends."
            ),
            "picks": [
                {"product_id": c["id"], "rationale": FALLBACK_RATIONALE.format(theme=theme)}
                for c in candidates[:3]
            ],
        }
        return {
            "result": result,
            "llm_calls": llm_calls,
            "total_tokens": total_tokens,
            "steps": _append_step(state, "generate", "fallback picks used (LLM unreliable)"),
        }

    return {
        "result": result,
        "llm_calls": llm_calls,
        "total_tokens": total_tokens,
        "steps": _append_step(state, "generate", f"{len(result['picks'])} picks grounded in candidates"),
    }


def store(state: dict) -> dict:
    started = state.get("started_at")
    duration_ms = int((time.monotonic() - started) * 1000) if started else 0

    if state.get("skip_reason"):
        steps = _append_step(state, "store", f"recorded skipped run ({state['skip_reason']})")
        _write_run(state, duration_ms, error=f"skipped: {state['skip_reason']}", steps=steps)
        return {"run_id": None, "steps": steps}

    result = state.get("result")
    if result is None:
        steps = _append_step(state, "store", "recorded run without recommendation")
        _write_run(state, duration_ms, error="no candidates to present", steps=steps)
        return {"run_id": None, "steps": steps}

    steps = _append_step(
        state, "store", f"stored recommendation with {len(result['picks'])} items"
    )

    db = SessionLocal()
    try:
        recommendation = Recommendation(
            user_id=state["user_id"],
            narrative=result["narrative"],
            summary=result["summary"],
            trigger_reason=state.get("trigger_reason"),
            source=state.get("source", "auto"),
            valid_until=utcnow_naive_delta(minutes=settings.reco_validity_minutes),
        )
        db.add(recommendation)
        db.flush()

        rank_map = {c["id"]: c for c in state.get("candidates", [])}
        for rank, pick in enumerate(result["picks"], start=1):
            product_id = pick["product_id"]
            candidate = rank_map.get(product_id, {})
            db.add(
                RecommendationItem(
                    recommendation_id=recommendation.id,
                    product_id=product_id,
                    rank=rank,
                    score=candidate.get("composite") or candidate.get("score"),
                    rationale=pick.get("rationale", ""),
                )
            )
        # invalidate any older cached recommendations for this user
        db.query(Recommendation).filter(
            Recommendation.user_id == state["user_id"],
            Recommendation.id != recommendation.id,
        ).update(
            {Recommendation.valid_until: utcnow_naive()},
            synchronize_session=False,
        )

        run = _write_run(
            state,
            duration_ms,
            error=None,
            steps=steps,
            db=db,
        )
        db.commit()
        return {"run_id": run.id, "recommendation_id": recommendation.id, "steps": steps}
    finally:
        db.close()


def _write_run(
    state: dict,
    duration_ms: int,
    error: str | None,
    steps: list,
    db=None,
) -> AgentRun:
    own_session = db is None
    session = db or SessionLocal()
    try:
        run = AgentRun(
            user_id=state.get("user_id"),
            trace_id=state["trace_id"],
            trigger=state.get("trigger", "auto"),
            steps=steps,
            llm_calls=state.get("llm_calls", 0),
            total_tokens=state.get("total_tokens", 0),
            duration_ms=duration_ms,
            error=error,
        )
        session.add(run)
        if own_session:
            session.commit()
            session.refresh(run)
        return run
    finally:
        if own_session:
            session.close()


# --------------------------------------------------------------------------- #
# parsing / validation helpers
# --------------------------------------------------------------------------- #
def _extract_generation(content: str) -> dict:
    from app.agent.profile import extract_json

    return extract_json(content)


def _validate_picks(data: dict, candidate_ids: set[int]):
    picks = data.get("picks", [])
    if not isinstance(picks, list) or len(picks) < 1:
        return None
    validated = []
    for pick in picks:
        if not isinstance(pick, dict):
            return None
        product_id = pick.get("product_id")
        if not isinstance(product_id, int) or product_id not in candidate_ids:
            return None
        validated.append(
            {
                "product_id": product_id,
                "rationale": str(pick.get("rationale", ""))[:400],
            }
        )
    if not validated:
        return None
    return validated


def _id_from_vector(vector_id: str) -> int | None:
    try:
        return int(vector_id.split(":")[-1])
    except (ValueError, IndexError):
        return None
