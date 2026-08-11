"""Cross-Site Request Forgery (CSRF) defense-in-depth.

The session cookie is already ``SameSite=Lax`` + ``HttpOnly``, which stops the
classic browser CSRF vector. This middleware adds the OWASP-recommended
Origin/Referer check as a second layer: state-changing requests whose
``Origin`` (or ``Referer``) host does not match the request host are rejected.

Non-browser clients (curl, the test client, server-to-server calls) do not send
these headers and are allowed through — enforcement targets browsers, where the
headers are always present on cross-site ``<form>``/``fetch`` posts.
"""
from urllib.parse import urlparse

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _origin_host(header_value: str) -> str | None:
    parsed = urlparse(header_value)
    host = (parsed.hostname or "").lower()
    if not host:
        return None
    if parsed.port and parsed.port not in (80, 443):
        return f"{host}:{parsed.port}"
    return host


def _request_host(request: Request) -> str:
    host = request.headers.get("host", "").strip().lower()
    return host.split(",")[0].strip()


class CSRFOriginCheckMiddleware(BaseHTTPMiddleware):
    """Reject cross-site state-changing requests via Origin/Referer check."""

    async def dispatch(self, request: Request, call_next):
        if request.method in MUTATING_METHODS:
            origin = request.headers.get("origin")
            referer = request.headers.get("referer")
            source = origin or referer
            if source:
                source_host = _origin_host(source)
                if source_host and source_host != _request_host(request):
                    return JSONResponse(
                        {"detail": "Cross-site request rejected (CSRF check failed)"},
                        status_code=403,
                    )
        return await call_next(request)
