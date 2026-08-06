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
from app.models import EmailDigest, User, UserEvent
from app.services import events as events_service
from app.services import recommendations as rec_service
from app.templating import templates
from app.utils import utcnow_naive

logger = logging.getLogger(__name__)

LOOKBACK_HOURS = 24


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
    subject = f"SmartReco: {rec.summary}"
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
            f"- {product.title} (${product.price}): {pick['item'].rationale}"
        )
    lines.append("")
    lines.append(f"See your full recommendations: {ctx['rec_url']}")
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
