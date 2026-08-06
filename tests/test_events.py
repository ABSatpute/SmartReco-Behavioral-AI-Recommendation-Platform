from app.database import SessionLocal
from app.models import Session, User, UserEvent


def test_ingest_valid_batch(client):
    resp = client.post(
        "/api/events/batch",
        json={
            "events": [
                {"event_type": "product_view", "entity_type": "product", "entity_id": "1"},
                {"event_type": "search", "entity_type": "query", "entity_id": "agentic ai"},
                {"event_type": "time_spent", "payload": {"seconds": 42}},
            ]
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["stored"] == 3
    assert "smartreco_session" in resp.headers["set-cookie"]

    db = SessionLocal()
    count = db.query(UserEvent).count()
    db.close()
    assert count == 3


def test_ingest_drops_invalid_types(client):
    resp = client.post(
        "/api/events/batch",
        json={
            "events": [
                {"event_type": "product_view", "entity_type": "product", "entity_id": "1"},
                {"event_type": "not_a_real_event"},
                {"event_type": ""},
            ]
        },
    )
    assert resp.status_code == 200
    assert resp.json()["stored"] == 1


def test_ingest_reuses_anonymous_session(client):
    first = client.post("/api/events/batch", json={"events": [{"event_type": "page_view"}]})
    cookie = first.cookies.get("smartreco_session")
    assert cookie is not None

    client.post("/api/events/batch", json={"events": [{"event_type": "page_view"}]})

    db = SessionLocal()
    sessions = db.query(Session).all()
    events = db.query(UserEvent).all()
    db.close()
    assert len(sessions) == 1
    assert {e.session_id for e in events} == {sessions[0].id}


def test_events_linked_to_user_after_login(client):
    client.post(
        "/auth/register",
        data={
            "email": "alice@example.com",
            "password": "supersecret",
            "full_name": "Alice",
            "mobile": "+91 90000 00006",
            "age": "26",
            "gender": "female",
        },
    )
    resp = client.post(
        "/api/events/batch", json={"events": [{"event_type": "product_view", "entity_id": "7"}]}
    )
    assert resp.status_code == 200

    db = SessionLocal()
    event = db.query(UserEvent).order_by(UserEvent.id.desc()).first()
    user = db.query(User).filter(User.email == "alice@example.com").first()
    db.close()
    assert event.user_id == user.id


def test_time_spent_duration_persisted(client):
    resp = client.post(
        "/api/events/batch",
        json={
            "events": [
                {
                    "event_type": "time_spent",
                    "entity_type": "page",
                    "entity_id": "/products/foo",
                    "payload": {"duration": 37},
                }
            ]
        },
    )
    assert resp.status_code == 200
    assert resp.json()["stored"] == 1

    db = SessionLocal()
    event = db.query(UserEvent).order_by(UserEvent.id.desc()).first()
    db.close()
    assert event.event_type == "time_spent"
    assert event.payload == {"duration": 37}
