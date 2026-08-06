"""In-memory sliding-window rate limiter for auth endpoints.

State lives in this process only; suitable for a single-instance deployment.
Keys are per-client-IP (and per-action) to bound brute-force attempts.
"""
import threading
import time
from collections import defaultdict, deque

_events: dict[str, deque] = defaultdict(deque)
_lock = threading.Lock()


def allow(key: str, *, limit: int, window_seconds: int) -> bool:
    """Return True if a request for `key` is within the rate limit."""
    now = time.monotonic()
    with _lock:
        events = _events[key]
        while events and now - events[0] >= window_seconds:
            events.popleft()
        if len(events) >= limit:
            return False
        events.append(now)
        return True


def reset(key: str) -> None:
    with _lock:
        _events.pop(key, None)
