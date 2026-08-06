"""Daily digest: select active users, run/reuse the agent, render + send a persuasive email.

Idempotency: at most one digest per user per day (guarded by email_digests rows).
SMTP is optional at runtime — without credentials the email is "sent" to the
console logger so dev/tests keep working (status still recorded as sent).
"""
import logging
import smtplib
from datetime import timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.models import BrowseSession, EmailDigest, Recommendation, SessionDigest, User, UserEvent
from app.services import events as events_service
from app.services import recommendations as rec_service
from app.templating import templates
from app.utils import utcnow_naive

logger = logging.getLogger(__name__)

LOOKBACK_HOURS = 24

SLOT_LABELS = {
    "1h": "Quick picks from your recent session",
    "6h": "Still on the fence? Here are your picks",
    "12h": "Last look — picks matched to your session",
}


def _already_digested_today(db: Session, user_id: int) -> bool:
    today = utcnow_naive().replace(hour=0, minute=0, second=0, microsecond=0)
    existing = (
        db.query(EmailDigest)
        .filter(
            EmailDigest.user_id == user_id,
            EmailDigest.status != "failed",
            EmailDigest.created_at >= today,
        )
        .first()
    )
    return existing is not None


def _candidate_user_ids(db: Session) -> list[int]:
    since = utcnow_naive() - timedelta(hours=LOOKBACK_HOURS)
    rows = (
        db.query(UserEvent.user_id)
        .filter(
            UserEvent.user_id.isnot(None),
            UserEvent.event_type.in_(events_service.MEANINGFUL_TYPES),
            UserEvent.occurred_at >= since,
        )
        .distinct()
        .all()
    )
    return [r[0] for r in rows]


def render_digest(rec, products: list[dict]) -> tuple[str, str, str]:
    """Returns (subject, html_body, text_body)."""
    subject = f"{rec.summary}"
    ctx = {
        "summary": rec.summary,
        "narrative": rec.narrative,
        "picks": products,
        "rec_url": f"{settings.app_base_url.rstrip('/')}/recommendations",
    }
    html = templates.env.get_template("emails/digest.html").render(**ctx)

    lines = [rec.narrative, "", "Top picks:"]
    for pick in products:
        product = pick["product"]
        if product is None:
            continue
        lines.append(
            f"# {product.title} (${product.price}) — {pick['item'].rationale}"
        )
    lines.append("")
    lines.append(f"See them all: {ctx['rec_url']}")
    text = "\n".join(lines)
    return subject, html, text


def send_email(to_email: str, subject: str, html: str, text: str) -> bool:
    if settings.email_backend == "resend":
        return _send_resend(to_email, subject, html, text)
    return _send_smtp(to_email, subject, html, text)


def send_telegram(chat_id: str, text: str) -> bool:
    if not settings.telegram_bot_token:
        logger.info("[dev-fallback] telegram message -> chat=%s", chat_id)
        return False
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    try:
        resp = httpx.post(
            url,
            json={
                "chat_id": chat_id,
                "text": text,
                "disable_web_page_preview": False,
            },
            timeout=20,
        )
        if resp.status_code >= 400:
            logger.error("Telegram API error %s: %s", resp.status_code, resp.text[:500])
            return False
        return True
    except Exception:  # noqa: BLE001 - failure must not break the digest job
        logger.exception("Telegram send failed to chat %s", chat_id)
        return False


def _send_resend(to_email: str, subject: str, html: str, text: str) -> bool:
    if not settings.resend_api_key:
        logger.info("[dev-fallback] digest email -> %s subject=%r", to_email, subject)
        return True

    try:
        resp = httpx.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {settings.resend_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "from": settings.email_from or "SmartReco <onboarding@resend.dev>",
                "to": [to_email],
                "subject": subject,
                "html": html,
                "text": text,
            },
            timeout=20,
        )
        if resp.status_code >= 400:
            logger.error(
                "Resend API error %s: %s", resp.status_code, resp.text[:500]
            )
            return False
        return True
    except Exception:  # noqa: BLE001 - sending failure must not break the job
        logger.exception("Resend send failed to %s", to_email)
        return False


def _send_smtp(to_email: str, subject: str, html: str, text: str) -> bool:
    if not settings.smtp_host:
        logger.info("[dev-fallback] digest email -> %s subject=%r", to_email, subject)
        return True

    sender = settings.email_from or "SmartReco <noreply@smartreco.dev>"
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to_email
    msg.attach(MIMEText(text, "plain"))
    msg.attach(MIMEText(html, "html"))
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as server:
            server.ehlo()
            if settings.smtp_user:
                server.starttls()
                server.login(settings.smtp_user, settings.smtp_password)
            server.sendmail(sender, [to_email], msg.as_string())
        return True
    except Exception:  # noqa: BLE001 - sending failure must not break the job
        logger.exception("SMTP send failed to %s", to_email)
        return False


def _deliver_for_user(session: Session, user: User, summary: dict) -> bool:
    """Build + send one user's digest. Returns True when an email was sent."""
    recommendation = rec_service.valid_latest(session, user.id)
    if recommendation is None:
        recommendation = rec_service.run(
            session, user, source="daily_digest", trigger="digest", force=True
        )
    if recommendation is None:
        summary["no_recommendation"] += 1
        return False

    products = rec_service.with_products(session, recommendation)
    if not products:
        summary["no_recommendation"] += 1
        return False

    subject, html, text = render_digest(recommendation, products)

    email_ok = True
    if "email" in settings.notification_channels_list:
        email_ok = send_email(user.email, subject, html, text)

    tg_ok = None
    if (
        "telegram" in settings.notification_channels_list
        and user.telegram_chat_id
    ):
        tg_ok = send_telegram(user.telegram_chat_id, text)

    # The email is the primary channel. Telegram is best-effort: a Telegram
    # failure is logged but must not fail the email digest.
    ok = email_ok

    digest = EmailDigest(
        user_id=user.id,
        subject=subject,
        body=text,
        status="sent" if ok else "failed",
        sent_at=utcnow_naive() if ok else None,
    )
    session.add(digest)
    session.commit()
    summary["sent" if ok else "failed"] += 1
    logger.info("Digest %s for user %s", digest.status, user.id)
    return ok


def run_digest(db: Session | None = None) -> dict:
    """Run the daily digest for every active user. Returns a summary dict."""
    own_session = db is None
    session = db or SessionLocal()
    summary = {
        "candidates": 0,
        "sent": 0,
        "failed": 0,
        "skipped_already": 0,
        "no_recommendation": 0,
    }
    try:
        user_ids = _candidate_user_ids(session)
        summary["candidates"] = len(user_ids)

        for user_id in user_ids:
            user = session.get(User, user_id)
            if user is None:
                continue
            if _already_digested_today(session, user_id):
                summary["skipped_already"] += 1
                continue

            try:
                handled = _deliver_for_user(session, user, summary)
            except Exception:  # noqa: BLE001 - one user must not break the batch
                logger.exception("Digest failed for user %s", user_id)
                session.rollback()
                session.add(
                    EmailDigest(
                        user_id=user_id,
                        subject="",
                        body="",
                        status="failed",
                    )
                )
                session.commit()
                summary["failed"] += 1

        return summary
    finally:
        if own_session:
            session.close()


# ---------------------------------------------------------------------------
# Session-based follow-ups (1h / 6h / 12h after a browsing session ends)
# ---------------------------------------------------------------------------

def _session_slots() -> list[str]:
    parts = settings.session_slots_hours.split(",")
    return [f"{int(p.strip())}h" for p in parts if p.strip()]


def _slot_hours(slot: str) -> int:
    return int(slot.rstrip("h"))


def _due_slot(db: Session, browse: BrowseSession, now) -> str | None:
    """First un-sent slot whose delay has elapsed, or None."""
    existing = {
        s.slot
        for s in db.query(SessionDigest).filter(
            SessionDigest.browse_session_id == browse.id
        )
    }
    for slot in _session_slots():
        if slot in existing:
            continue
        if now >= browse.last_seen_at + timedelta(hours=_slot_hours(slot)):
            return slot
    return None


def _deliver_session_slot(
    db: Session, browse: BrowseSession, user: User, slot: str, now
) -> str:
    """Generate (once per session, then reuse) + send. Returns status."""
    if events_service.session_meaningful_count(db, browse.id) <= 0:
        _record_session_slot(db, browse, user, slot, None, "skipped", now)
        return "skipped"

    # Reuse the recommendation built for this session when one exists.
    rec = None
    prior = (
        db.query(SessionDigest)
        .filter(
            SessionDigest.browse_session_id == browse.id,
            SessionDigest.recommendation_id.isnot(None),
        )
        .order_by(SessionDigest.created_at.desc())
        .first()
    )
    if prior is not None:
        rec = db.get(Recommendation, prior.recommendation_id)
    if rec is None:
        rec = rec_service.run(
            db,
            user,
            source="session_digest",
            trigger=f"session_{slot}",
            force=True,
            browse_session_id=browse.id,
        )
    if rec is None:
        _record_session_slot(db, browse, user, slot, None, "skipped", now)
        return "skipped"

    products = rec_service.with_products(db, rec)
    if not products:
        _record_session_slot(db, browse, user, slot, rec.id, "skipped", now)
        return "skipped"

    subject, html, text = render_digest(rec, products)
    subject = f"{SLOT_LABELS[slot]}: {subject}"

    email_ok = True
    if "email" in settings.notification_channels_list:
        email_ok = send_email(user.email, subject, html, text)
    if (
        "telegram" in settings.notification_channels_list
        and user.telegram_chat_id
    ):
        send_telegram(user.telegram_chat_id, text)

    status = "sent" if email_ok else "failed"
    _record_session_slot(db, browse, user, slot, rec.id, status, now)
    return status


def _record_session_slot(
    db: Session, browse: BrowseSession, user: User, slot: str,
    rec_id: int | None, status: str, now,
) -> None:
    existing = (
        db.query(SessionDigest)
        .filter(
            SessionDigest.browse_session_id == browse.id,
            SessionDigest.slot == slot,
        )
        .first()
    )
    if existing is not None:
        return
    db.add(
        SessionDigest(
            browse_session_id=browse.id,
            user_id=user.id,
            slot=slot,
            recommendation_id=rec_id,
            status=status,
            sent_at=now if status == "sent" else None,
        )
    )
    db.commit()


def run_session_digests(db: Session | None = None) -> dict:
    """Sweep ended browse sessions and send whichever follow-up slot is due.

    One slot per session per sweep (so 1h → 6h → 12h progress in order), newest
    session first. Email is primary; Telegram is best-effort.
    """
    own_session = db is None
    session = db or SessionLocal()
    summary = {"slots_sent": 0, "slots_skipped": 0, "slots_failed": 0}
    try:
        now = utcnow_naive()
        gap = timedelta(minutes=settings.session_gap_minutes)
        sessions = (
            session.query(BrowseSession)
            .filter(
                BrowseSession.user_id.isnot(None),
                BrowseSession.last_seen_at <= now - gap,
            )
            .order_by(BrowseSession.last_seen_at.desc())
            .all()
        )

        seen_users: set[int] = set()
        for browse in sessions:
            if browse.user_id in seen_users:
                continue
            user = session.get(User, browse.user_id)
            if user is None:
                continue
            slot = _due_slot(session, browse, now)
            if slot is None:
                continue
            try:
                status = _deliver_session_slot(session, browse, user, slot, now)
            except Exception:  # noqa: BLE001 - one session must not break the sweep
                logger.exception("Session digest failed for session %s", browse.id)
                session.rollback()
                status = "failed"
            summary[f"slots_{status}"] = summary.get(f"slots_{status}", 0) + 1
            seen_users.add(browse.user_id)

        return summary
    finally:
        if own_session:
            session.close()
