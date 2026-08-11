"""One-shot flash messages carried between redirects via a short-lived cookie.

The cookie is deliberately non-HttpOnly so the client-side toast script can
read it and clear it immediately after showing it. Values are HMAC-signed with
``settings.secret_key`` so they cannot be forged by a client.
"""
import base64
import hashlib
import hmac
import json

from fastapi import Request, Response

from app.config import settings

FLASH_COOKIE = "smartreco_flash"
FLASH_TTL_SECONDS = 10


def _sign(value: str) -> str:
    return hmac.new(
        settings.secret_key.encode("utf-8"), value.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def set_flash(response: Response, message: str, type: str = "success") -> None:
    payload = base64.urlsafe_b64encode(
        json.dumps({"message": message, "type": type}).encode("utf-8")
    ).decode("ascii")
    response.set_cookie(
        key=FLASH_COOKIE,
        value=f"{payload}.{_sign(payload)}",
        max_age=FLASH_TTL_SECONDS,
        httponly=False,
        samesite="lax",
        path="/",
        secure=settings.app_env == "production",
    )


def get_flash(request: Request) -> dict | None:
    raw = request.cookies.get(FLASH_COOKIE)
    if not raw:
        return None
    if "." not in raw:
        return None
    payload, signature = raw.rsplit(".", 1)
    if not hmac.compare_digest(_sign(payload), signature):
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")).decode("utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict) or "message" not in data:
        return None
    return {"message": str(data["message"]), "type": str(data.get("type", "success"))}
