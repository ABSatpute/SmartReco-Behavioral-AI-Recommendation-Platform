import logging
import secrets
import threading
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database import Base, engine
from app.observability import current_trace_id, setup_logging_and_langsmith, trace_id_var
from app.router import admin, api, auth, cron, pages
from app.scheduler import shutdown as shutdown_scheduler
from app.scheduler import start as start_scheduler

logger = logging.getLogger(__name__)


def _startup_digest_catchup() -> None:
    """One best-effort digest shortly after boot (free-tier wake-up path).

    Guarded by the per-user per-day check inside run_digest, so a cold start
    that follows a missed scheduled slot still delivers within seconds."""
    time.sleep(20)
    try:
        from app.services.digest import run_digest, run_session_digests

        result = run_digest()
        logger.info("Startup digest catch-up: %s", result)
        result = run_session_digests()
        logger.info("Startup session-digest catch-up: %s", result)
    except Exception:  # noqa: BLE001 - never block boot on digest work
        logger.exception("Startup digest catch-up failed")


@asynccontextmanager
async def lifespan(_: FastAPI):
    setup_logging_and_langsmith()
    Base.metadata.create_all(bind=engine)
    start_scheduler()
    if settings.app_env != "test":
        threading.Thread(target=_startup_digest_catchup, daemon=True).start()
    yield
    shutdown_scheduler()


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)


@app.get("/healthz")
def healthz() -> Response:
    from sqlalchemy import text

    from app.database import engine

    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return Response(content='{"status":"ok"}', media_type="application/json")
    except Exception:
        return Response(content='{"status":"unhealthy"}', status_code=503, media_type="application/json")


@app.middleware("http")
async def no_cache_middleware(request: Request, call_next):
    response: Response = await call_next(request)
    content_type = response.headers.get("content-type", "")
    if "text/html" in content_type:
        response.headers["Cache-Control"] = "no-store"
    elif request.url.path.startswith("/static"):
        response.headers.setdefault("Cache-Control", "public, max-age=0, must-revalidate")
    return response


@app.middleware("http")
async def trace_id_middleware(request: Request, call_next):
    trace_id = request.headers.get("x-trace-id")
    if not trace_id:
        trace_id = f"{settings.app_env[:4]}-{secrets.token_hex(8)}"
    token = trace_id_var.set(trace_id)
    request.state.trace_id = trace_id
    try:
        response: Response = await call_next(request)
        response.headers["x-trace-id"] = current_trace_id()
        return response
    finally:
        trace_id_var.reset(token)

app.mount("/static", StaticFiles(directory="static"), name="static")
app.include_router(pages.router)
app.include_router(auth.router)
app.include_router(api.router)
app.include_router(admin.router)
app.include_router(cron.router)
