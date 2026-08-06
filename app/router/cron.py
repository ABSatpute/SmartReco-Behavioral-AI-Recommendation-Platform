"""Cron-triggered endpoints (for Render Cron Jobs / external schedulers).

`/cron/digest` wakes a spun-down free-tier instance and runs the daily digest
at whatever time the cron fires. Protected by a shared secret so the general
public cannot spam the digest.
"""
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.services import digest as digest_service

router = APIRouter(prefix="/cron")


def _authorized(
    x_cron_token: str | None = Header(default=None),
    token: str | None = Query(default=None),
) -> None:
    if not settings.cron_secret:
        raise HTTPException(status_code=403, detail="Cron not configured.")
    if x_cron_token != settings.cron_secret and token != settings.cron_secret:
        raise HTTPException(status_code=403, detail="Unauthorized.")


@router.get("/digest")
def cron_digest(
    request: Request,
    _: None = Depends(_authorized),
    db: Session = Depends(get_db),
):
    return digest_service.run_digest(db)


@router.get("/sessions")
def cron_session_digests(
    request: Request,
    _: None = Depends(_authorized),
    db: Session = Depends(get_db),
):
    return digest_service.run_session_digests(db)
