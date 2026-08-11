from datetime import UTC, datetime, timedelta


def utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def utcnow_naive_delta(minutes: int = 0) -> datetime:
    return utcnow_naive() + timedelta(minutes=minutes)
