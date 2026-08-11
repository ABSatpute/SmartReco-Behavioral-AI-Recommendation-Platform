"""Recommendation service: trigger policy, cache, and agent execution.

Efficiency rules (judged criteria — no LLM call on a bare page view):
- A cached recommendation within `valid_until` is served without any agent run.
- The agent runs only when the trigger policy fires:
  1. >= min_events_threshold meaningful events since the last run,
  2. cooldown elapsed AND at least one new meaningful event,
  3. the user explicitly refreshes, or
  4. the daily digest scheduler fires (later milestone).
"""
import logging
import threading
import time
import uuid
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.agent.graph import agent_graph
from app.config import settings
from app.database import SessionLocal
from app.models import AgentRun, Product, Recommendation, RecommendationItem, User
from app.observability import current_trace_id
from app.services import events as events_service
from app.utils import utcnow_naive

logger = logging.getLogger(__name__)

_RUNNING_USERS: set[int] = set()
_RUNNING_LOCK = threading.Lock()


def valid_latest(db: Session, user_id: int) -> Recommendation | None:
    now = utcnow_naive()
    return (
        db.query(Recommendation)
        .filter(Recommendation.user_id == user_id)
        .filter(
            (Recommendation.valid_until.is_(None))
            | (Recommendation.valid_until > now)
        )
        .order_by(Recommendation.created_at.desc())
        .first()
    )


def latest(db: Session, user_id: int) -> Recommendation | None:
    return (
        db.query(Recommendation)
        .filter(Recommendation.user_id == user_id)
        .order_by(Recommendation.created_at.desc())
        .first()
    )


def last_run(db: Session, user_id: int) -> AgentRun | None:
    return (
        db.query(AgentRun)
        .filter(AgentRun.user_id == user_id)
        .order_by(AgentRun.created_at.desc())
        .first()
    )


def should_run(db: Session, user_id: int, source: str = "auto", force: bool = False) -> bool:
    if force:
        return True
    previous = last_run(db, user_id)
    since = previous.created_at if previous else datetime.min
    new_events = events_service.meaningful_count(db, user_id, since=since)

    if new_events >= settings.min_events_threshold:
        return True
    if previous is None and new_events >= 1:
        return True
    if previous is not None:
        elapsed = utcnow_naive() - previous.created_at
        if elapsed >= timedelta(minutes=settings.min_reco_run_interval_minutes) and new_events >= 1:
            return True
    return False


def _trigger_reason(db: Session, user_id: int, events: list | None = None) -> str:
    if events is None:
        previous = last_run(db, user_id)
        since = previous.created_at if previous else datetime.min
        events = events_service.recent(db, user_id, since=since, limit=100)
    counts: dict[str, int] = {}
    for event in events:
        counts[event.event_type] = counts.get(event.event_type, 0) + 1
    parts = []
    if counts.get("product_view"):
        parts.append(f"viewed {counts['product_view']} products")
    if counts.get("search"):
        parts.append(f"searched {counts['search']} times")
    if counts.get("product_click"):
        parts.append(f"clicked {counts['product_click']} products")
    if counts.get("add_to_cart"):
        parts.append(f"added {counts['add_to_cart']} items to cart")
    base = "You " + ", ".join(parts) if parts else "Your recent browsing"
    return f"{base} since your last update."


def run(
    db: Session,
    user: User,
    source: str = "auto",
    trigger: str | None = None,
    force: bool = False,
    browse_session_id: int | None = None,
) -> Recommendation | None:
    """Execute the LangGraph agent workflow and persist results + trace.

    When ``browse_session_id`` is given, the agent reasons only about that
    browsing session (fresh-intent recommendations); otherwise it uses the
    user's recent activity.
    """
    trigger = trigger or ("manual" if source == "manual" else "event_threshold")
    if browse_session_id is not None:
        events = events_service.for_browse_session(db, browse_session_id, limit=100)
    else:
        previous = last_run(db, user.id)
        since = previous.created_at if previous else None
        events = events_service.recent(db, user.id, since=since, limit=100)

    state = {
        "user_id": user.id,
        "trace_id": current_trace_id() or uuid.uuid4().hex[:16],
        "trigger": trigger,
        "source": source,
        "trigger_reason": _trigger_reason(db, user.id, events),
        "started_at": time.monotonic(),
        "events": [events_service.serialize(e) for e in events],
        "event_ids": [e.id for e in events],
        "event_summary": events_service.summary_text(events),
        "user_context": {
            "age": user.age,
            "gender": user.gender,
        }
        if (user.age or user.gender)
        else None,
        "steps": [],
        "llm_calls": 0,
        "total_tokens": 0,
        "attempts": 0,
        "fallback_used": False,
    }

    try:
        agent_graph.invoke(state)
    except Exception as exc:
        logger.exception("Agent run failed for user %s", user.id)
        db.add(
            AgentRun(
                user_id=user.id,
                trace_id=state["trace_id"],
                trigger=trigger,
                steps=state.get("steps", []),
                llm_calls=state.get("llm_calls", 0),
                total_tokens=state.get("total_tokens", 0),
                duration_ms=0,
                error=f"unhandled: {exc}",
            )
        )
        db.commit()

    return valid_latest(db, user.id)


def ensure(db: Session, user: User, source: str = "auto") -> Recommendation | None:
    """Serve the cached recommendation if valid; otherwise run when policy allows."""
    cached = valid_latest(db, user.id)
    if cached is not None:
        return cached
    if should_run(db, user.id, source=source):
        run(db, user, source=source)
        return valid_latest(db, user.id)
    return None


def refresh(db: Session, user: User) -> Recommendation | None:
    """Force a fresh agent run (manual refresh trigger)."""
    run(db, user, source="manual", trigger="manual", force=True)
    return valid_latest(db, user.id)


def with_products(db: Session, rec: Recommendation | None) -> list[dict]:
    if rec is None:
        return []
    items = (
        db.query(RecommendationItem)
        .filter(RecommendationItem.recommendation_id == rec.id)
        .order_by(RecommendationItem.rank.asc())
        .all()
    )
    products = {
        p.id: p
        for p in db.query(Product)
        .filter(Product.id.in_([i.product_id for i in items]))
        .all()
    }
    return [
        {"item": item, "product": products.get(item.product_id)}
        for item in items
        if item.product_id in products
    ]


def schedule_background(user_id: int) -> bool:
    """Queue a fresh agent run for ``user_id`` if one isn't already in flight.

    The trigger policy (event threshold + cooldown) is re-checked inside the
    worker thread, so this only ever produces *new* recommendations — which is
    what the browser notification watches for. Returns True when a thread was
    started.
    """
    with _RUNNING_LOCK:
        if user_id in _RUNNING_USERS:
            return False
        _RUNNING_USERS.add(user_id)
    threading.Thread(
        target=_background_worker, args=(user_id,), daemon=True, name=f"reco-{user_id}"
    ).start()
    return True


def _background_worker(user_id: int) -> None:
    try:
        db = SessionLocal()
        try:
            user = db.get(User, user_id)
            if user is None or not should_run(db, user_id, source="auto"):
                return
            run(db, user, source="auto")
        finally:
            db.close()
    except Exception:
        logger.exception("Background recommendation run failed for user %s", user_id)
    finally:
        with _RUNNING_LOCK:
            _RUNNING_USERS.discard(user_id)
