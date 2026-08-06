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


@pytest.fixture(autouse=True)
def _tables():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c
