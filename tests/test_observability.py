import logging
import json

from app.database import SessionLocal
from app.models import AgentRun, User
from app.observability import JsonFormatter, current_trace_id, trace_id_var


def _register_and_make_admin(client, email="admin@x.com"):
    client.post(
        "/auth/register",
        data={
            "email": email,
            "password": "adminpass1",
            "full_name": "Admin",
            "mobile": "+91 90000 00007",
            "age": "40",
            "gender": "male",
        },
        follow_redirects=False,
    )
    db = SessionLocal()
    user = db.query(User).filter(User.email == email).first()
    user.role = "admin"
    db.commit()
    db.close()


def _register_regular(client, email="user@x.com"):
    client.post(
        "/auth/register",
        data={
            "email": email,
            "password": "userpass1",
            "full_name": "User",
            "mobile": "+91 90000 00008",
            "age": "24",
            "gender": "female",
        },
        follow_redirects=False,
    )


def test_trace_id_middleware_injects_and_echoes(client):
    resp = client.get("/")
    assert resp.status_code in (200, 302)
    echo = resp.headers.get("x-trace-id")
    assert echo
    assert len(echo) > 8


def test_trace_id_respected_from_request_header(client):
    resp = client.get("/", headers={"x-trace-id": "custom-trace-123"})
    assert resp.headers.get("x-trace-id") == "custom-trace-123"


def test_trace_id_contextvar_isolation(client):
    token = trace_id_var.set("thread-a")
    try:
        client.get("/")
        assert current_trace_id() == "thread-a"
    finally:
        trace_id_var.reset(token)


def test_observability_page_requires_admin(client):
    resp = client.get("/admin/observability")
    assert resp.status_code in (401, 303)

    _register_regular(client)
    resp = client.get("/admin/observability")
    assert resp.status_code == 403


def test_observability_page_renders_admin(client):
    _register_and_make_admin(client)
    db = SessionLocal()
    db.add(
        AgentRun(
            user_id=db.query(User).first().id,
            trace_id="trace-abc",
            trigger="manual",
            steps=[],
            llm_calls=2,
            total_tokens=100,
            duration_ms=50,
        )
    )
    db.commit()
    db.close()

    resp = client.get("/admin/observability")
    assert resp.status_code == 200
    assert "Observability" in resp.text
    assert "trace-abc" in resp.text


def test_json_formatter_output_is_parseable():
    record = logging.LogRecord(
        name="app.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello %s",
        args=("world",),
        exc_info=None,
    )
    line = JsonFormatter().format(record)
    payload = json.loads(line)
    assert payload["message"] == "hello world"
    assert payload["level"] == "INFO"
    assert "trace_id" not in payload

    token = trace_id_var.set("trace-xyz")
    try:
        line2 = JsonFormatter().format(record)
        assert json.loads(line2)["trace_id"] == "trace-xyz"
    finally:
        trace_id_var.reset(token)