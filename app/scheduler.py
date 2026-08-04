"""APScheduler wiring for the daily digest (proactive delivery bonus).

Started in the FastAPI lifespan. One cron job per day at DIGEST_TIME in
DIGEST_TIMEZONE. The job runs in a scheduler thread (not the request thread).
"""
import logging
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import settings
from app.services.digest import run_digest

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler(timezone=ZoneInfo(settings.digest_timezone))


def _parse_time(value: str) -> tuple[int, int]:
    try:
        hour, minute = value.strip().split(":")
        return int(hour), int(minute)
    except (ValueError, AttributeError):
        return 9, 0


def _digest_job() -> None:
    try:
        result = run_digest()
        logger.info("Daily digest complete: %s", result)
    except Exception:  # noqa: BLE001 - a scheduler job must never crash silently
        logger.exception("Daily digest job failed")


def start() -> None:
    if scheduler.running:
        return
    hour, minute = _parse_time(settings.digest_time)
    scheduler.add_job(
        _digest_job,
        CronTrigger(hour=hour, minute=minute),
        id="daily_digest",
        replace_existing=True,
    )
    scheduler.start()
    logger.info(
        "Scheduler started: daily digest at %s (%s)",
        settings.digest_time,
        settings.digest_timezone,
    )


def shutdown() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
