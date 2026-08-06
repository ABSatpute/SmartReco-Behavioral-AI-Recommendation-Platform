"""Event query helpers used by the agent trigger policy and analysis node."""
from datetime import datetime

from sqlalchemy.orm import Session

from app.models import UserEvent

MEANINGFUL_TYPES = {
    "product_view",
    "product_click",
    "category_click",
    "search",
    "add_to_cart",
}


def meaningful_count(db: Session, user_id: int, since: datetime | None = None) -> int:
    q = db.query(UserEvent).filter(
        UserEvent.user_id == user_id, UserEvent.event_type.in_(MEANINGFUL_TYPES)
    )
    if since is not None:
        q = q.filter(UserEvent.occurred_at > since)
    return q.count()


def recent(db: Session, user_id: int, since: datetime | None = None, limit: int = 100) -> list[UserEvent]:
    q = db.query(UserEvent).filter(UserEvent.user_id == user_id)
    if since is not None:
        q = q.filter(UserEvent.occurred_at > since)
    return q.order_by(UserEvent.occurred_at.desc()).limit(limit).all()


def for_browse_session(db: Session, browse_session_id: int, limit: int = 100) -> list[UserEvent]:
    """All tracked activity from a single browsing session (oldest first)."""
    return (
        db.query(UserEvent)
        .filter(UserEvent.browse_session_id == browse_session_id)
        .order_by(UserEvent.occurred_at.asc())
        .limit(limit)
        .all()
    )


def session_meaningful_count(db: Session, browse_session_id: int) -> int:
    return (
        db.query(UserEvent)
        .filter(
            UserEvent.browse_session_id == browse_session_id,
            UserEvent.event_type.in_(MEANINGFUL_TYPES),
        )
        .count()
    )


def serialize(event: UserEvent) -> dict:
    payload = dict(event.payload or {})
    return {
        "type": event.event_type,
        "entity_type": event.entity_type,
        "entity_id": event.entity_id,
        "payload": payload,
    }


def summary_text(events: list[UserEvent]) -> str:
    lines = []
    for e in events:
        detail = ""
        if e.entity_type == "search" or e.event_type == "search":
            detail = f" searched for {e.entity_id!r}"
        elif e.entity_type == "product":
            detail = f" {e.event_type.replace('_', ' ')} on product {e.entity_id}"
        elif e.entity_type == "category":
            detail = f" explored category {e.entity_id!r}"
        payload = e.payload or {}
        if "duration" in payload:
            detail += f" ({payload['duration']}s)"
        lines.append(f"- {e.event_type}{detail}".strip())
    return "\n".join(lines)
