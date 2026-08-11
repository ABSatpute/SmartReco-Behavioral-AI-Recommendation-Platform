import os

os.environ.setdefault(
    "TEST_DATABASE_URL", "postgresql://smartreco:smartreco@localhost:5432/smartreco_test"
)
os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]
os.environ["APP_ENV"] = "test"
os.environ["SMTP_HOST"] = ""  # isolate email tests from real SMTP credentials
os.environ["EMAIL_BACKEND"] = "smtp"  # never hit real Resend/SMTP during tests

import pytest
from fastapi.testclient import TestClient

from app.database import Base, engine
from app.main import app


def _release_stale_connections() -> None:
    """Terminate idle-in-transaction sessions that hold locks and would
    deadlock the per-test DDL reset (DROP TABLE needs AccessExclusiveLock
    but a lingering SELECT keeps an AccessShareLock open)."""
    from sqlalchemy import text

    with engine.connect() as conn:
        conn.execute(
            text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = current_database() "
                "AND pid <> pg_backend_pid() AND state = 'idle in transaction'"
            )
        )
        conn.commit()


@pytest.fixture(autouse=True)
def _tables():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    _release_stale_connections()
    Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c
