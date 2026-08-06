"""One-shot flash messages carried between redirects via a short-lived cookie.

The cookie is deliberately non-HttpOnly so the client-side toast script can
read it and clear it immediately after showing it.
"""
import base64
import json

from fastapi import Request, Response

FLASH_COOKIE = "smartreco_flash"
FLASH_TTL_SECONDS = 10


def set_flash(response: Response, message: str, type: str = "success") -> None:
    payload = base64.urlsafe_b64encode(
        json.dumps({"message": message, "type": type}).encode("utf-8")
    ).decode("ascii")
    response.set_cookie(
        key=FLASH_COOKIE,
        value=payload,
        max_age=FLASH_TTL_SECONDS,
        httponly=False,
        samesite="lax",
        path="/",
    )


def get_flash(request: Request) -> dict | None:
    raw = request.cookies.get(FLASH_COOKIE)
    if not raw:
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(raw.encode("ascii")).decode("utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict) or "message" not in data:
        return None
    return {"message": str(data["message"]), "type": str(data.get("type", "success"))}
