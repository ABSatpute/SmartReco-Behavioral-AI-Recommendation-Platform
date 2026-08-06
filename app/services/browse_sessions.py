"""Browsing-session bookkeeping.

A BrowseSession is a stretch of browsing activity (logged-in user or guest
session_key) separated from the next by SESSION_GAP_MINUTES of inactivity.
Events are attached to the current BrowseSession at ingest time so the agent
can reason about "the user's last session" specifically.
"""
from datetime import timedelta

from sqlalchemy.orm import Session

from app.config import settings
from app.models import BrowseSession
from app.utils import utcnow_naive


def _gap() -> timedelta:
    return timedelta(minutes=settings.session_gap_minutes)


def active(db: Session, user_id: int | None, session_key: str | None) -> BrowseSession | None:
    """Most recent BrowseSession that is still within the inactivity gap."""
    now = utcnow_naive()
    q = db.query(BrowseSession).filter(
        BrowseSession.ended_at.is_(None),
        BrowseSession.last_seen_at >= now - _gap(),
    )
    if user_id is not None:
        q = q.filter(BrowseSession.user_id == user_id)
    elif session_key:
        q = q.filter(BrowseSession.session_key == session_key)
    else:
        return None
    return q.order_by(BrowseSession.last_seen_at.desc()).first()


def touch_or_create(db: Session, user_id: int | None, session_key: str | None) -> BrowseSession:
    """Reuse the active session for this identity, else start a new one."""
    now = utcnow_naive()
    current = active(db, user_id, session_key)
    if current is not None:
        current.last_seen_at = now
        db.commit()
        db.refresh(current)
        return current

    browse = BrowseSession(
        user_id=user_id if user_id is not None else None,
        session_key=None if user_id is not None else session_key,
        started_at=now,
        last_seen_at=now,
    )
    db.add(browse)
    db.commit()
    db.refresh(browse)
    return browse


def close_stale(db: Session, user_id: int | None, session_key: str | None) -> None:
    """Mark sessions whose gap has elapsed as ended (idempotent)."""
    now = utcnow_naive()
    q = db.query(BrowseSession).filter(
        BrowseSession.ended_at.is_(None),
        BrowseSession.last_seen_at < now - _gap(),
    )
    if user_id is not None:
        q = q.filter(BrowseSession.user_id == user_id)
    elif session_key:
        q = q.filter(BrowseSession.session_key == session_key)
    else:
        return
    q.update({BrowseSession.ended_at: now}, synchronize_session=False)
    db.commit()
