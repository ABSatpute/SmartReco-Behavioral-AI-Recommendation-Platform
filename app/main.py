import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database import Base, engine
from app.observability import current_trace_id, setup_logging_and_langsmith, trace_id_var
from app.router import admin, api, auth, pages
from app.scheduler import shutdown as shutdown_scheduler
from app.scheduler import start as start_scheduler


@asynccontextmanager
async def lifespan(_: FastAPI):
    setup_logging_and_langsmith()
    Base.metadata.create_all(bind=engine)
    start_scheduler()
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
