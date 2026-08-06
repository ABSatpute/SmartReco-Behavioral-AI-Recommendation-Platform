"""APScheduler wiring for the daily digest (proactive delivery bonus).

Started in the FastAPI lifespan. The digest runs on a 30-minute cadence so any
wake-up of the (free-tier, spin-down) web instance delivers within 30 minutes;
a per-user per-day guard in run_digest keeps it at one digest a day. Exact-time
delivery can be added separately with a Render Cron Job hitting /cron/digest.
"""
import logging
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.config import settings
from app.services.digest import run_digest

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler(timezone=ZoneInfo(settings.digest_timezone))


def _digest_job() -> None:
    try:
        result = run_digest()
        logger.info("Digest run complete: %s", result)
    except Exception:  # noqa: BLE001 - a scheduler job must never crash silently
        logger.exception("Digest job failed")


def start() -> None:
    if scheduler.running:
        return
    scheduler.add_job(
        _digest_job,
        IntervalTrigger(minutes=30),
        id="digest",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("Scheduler started: digest every 30 minutes (%s)", settings.digest_timezone)


def shutdown() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
