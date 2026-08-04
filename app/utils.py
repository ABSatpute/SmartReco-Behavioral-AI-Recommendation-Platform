from datetime import datetime, timedelta, timezone


def utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def utcnow_naive_delta(minutes: int = 0) -> datetime:
    return utcnow_naive() + timedelta(minutes=minutes)
