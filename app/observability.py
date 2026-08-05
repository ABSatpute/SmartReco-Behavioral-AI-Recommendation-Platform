"""Observability (M7): trace-id propagation, structured JSON logging, LangSmith.

Design:
- A `ContextVar` carries a `trace_id` through a request/agent run, so every log
  line and downstream call is correlated automatically.
- Logs are emitted as structured JSON (single-per-line) with an optional
  `trace_id` and `app_env` field, making them ingestible by cloud log backends.
- LangSmith is *gated* by config: enabled only when `LANGSMITH_API_KEY` is set,
  so the app keeps working (and tests stay green) with zero external deps.
- `setup_logging_and_langsmith()` is idempotent and importable, so the CLI and
  the FastAPI lifespan can both opt in without side effects on import.
"""
import contextvars
import json
import logging
import os
import sys

from app.config import settings

trace_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "trace_id", default=""
)


def current_trace_id() -> str:
    return trace_id_var.get() or ""


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%SZ"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "app_env": settings.app_env,
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        trace_id = current_trace_id()
        if trace_id:
            payload["trace_id"] = trace_id
        extras = getattr(record, "fields", None)
        if extras:
            payload["fields"] = extras
        return json.dumps(payload, default=str)


def _install_json_handler(root: logging.Logger) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.handlers = [handler]
    root.setLevel(logging.INFO)


def _configure_verbose_loggers() -> None:
    """Quiet chatty third-party loggers so JSON output stays readable."""
    for name in (
        "httpcore",
        "httpx",
        "urllib3",
        "openai",
        "pinecone",
        "langgraph",
    ):
        logging.getLogger(name).setLevel(logging.WARNING)


def _gate_langsmith() -> None:
    """Enable LangSmith tracing only when an API key is configured."""
    key = settings.langsmith_api_key.strip()
    if not key:
        return
    global _langsmith_enabled
    try:
        from langsmith.utils import get_tracer_project  # noqa: F401
        import langsmith  # noqa: F401

        # langgraph reads these env vars to attach run/mid-run metadata.
        os.environ["LANGSMITH_API_KEY"] = key
        os.environ["LANGSMITH_TRACING"] = "true"
        if settings.app_project_name:
            os.environ["LANGSMITH_PROJECT"] = settings.app_project_name
        _langsmith_enabled = True
        logging.getLogger(__name__).info("LangSmith tracing enabled")
    except Exception:  # noqa: BLE001 - never block startup on a tracing addon
        _langsmith_enabled = False
        logging.getLogger(__name__).warning(
            "LangSmith requested but unavailable; running without tracing"
        )


_langsmith_enabled = False


def langsmith_enabled() -> bool:
    return _langsmith_enabled


def setup_logging_and_langsmith() -> None:
    """Idempotently install structured JSON logging and (opt-in) LangSmith."""
    root = logging.getLogger()
    if not any(isinstance(h.formatter, JsonFormatter) for h in root.handlers):
        _install_json_handler(root)
    _configure_verbose_loggers()
    _gate_langsmith()