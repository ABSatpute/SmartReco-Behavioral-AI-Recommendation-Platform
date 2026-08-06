import hashlib
from datetime import timedelta

import pytest

from app.agent import nodes
from app.database import SessionLocal
from app.models import AgentRun, BrowseSession, SessionDigest, User, UserEvent
from app.services import browse_sessions as browse_service
from app.services import digest as digest_service
from app.services import mesh as mesh_service
from app.services import products as product_service
from app.utils import utcnow_naive
from app.vector_store import InMemoryVectorStore

PROFILE_JSON = (
    '{"themes": ["AI engineering"], '
    '"keywords": ["rag", "llm", "vector", "search"], '
    '"engagement": "high", "urgency": ["active search"], '
    '"search_intents": ["learn retrieval augmented generation"]}'
)
GENERATION_JSON = (
    '{"summary": "Your next step in RAG", '
    '"narrative": "You were exploring retrieval and search. These picks level you up.", '
    '"picks": ['
    '  {"product_id": 1, "rationale": "directly matches your RAG interest"},'
    '  {"product_id": 2, "rationale": "a great companion course"}'
    "]}"
)


def fake_embed(texts, model=None):
    out = []
    for text in texts:
        h = hashlib.sha256(text.encode()).digest()
        out.append([((h[i] % 256) / 128) - 1 for i in range(8)])
    return out


def fake_chat_meta(messages, model=None, **kwargs):
    system = messages[0]["content"]
    if "behavioral analyst" in system:
        return PROFILE_JSON, 42
    return GENERATION_JSON, 99


@pytest.fixture()
def vector_store(monkeypatch):
    store = InMemoryVectorStore()
    monkeypatch.setattr(product_service, "get_vector_store", lambda: store)
    monkeypatch.setattr(nodes, "get_vector_store", lambda: store)
    monkeypatch.setattr(mesh_service, "embed", fake_embed)
    monkeypatch.setattr(
        mesh_service, "embed_one", lambda text, model=None: fake_embed([text])[0]
    )
    monkeypatch.setattr(mesh_service, "chat_meta", fake_chat_meta)
    return store


def _seed_catalog(n=3) -> None:
    db = SessionLocal()
    try:
        for i in range(n):
            product_service.create_product(
                db,
                {
                    "title": f"Course {i + 1}: Building RAG Systems",
                    "category": "AI Engineering",
                    "tags": ["rag", "llm", "vector"],
                    "price": 49.0 + i,
                    "level": "beginner",
                    "description": f"Hands-on retrieval augmented generation, part {i + 1}.",
                },
            )
    finally:
        db.close()


def _register_user(client, email="sess@x.com"):
    client.post(
        "/auth/register",
        data={
            "email": email,
            "password": "sesspass1",
            "full_name": "Session",
            "mobile": "+91 90000 00021",
            "age": "31",
            "gender": "female",
        },
        follow_redirects=False,
    )
    db = SessionLocal()
    user = db.query(User).filter(User.email == email).first()
    db.close()
    return user


def _make_browse_session(user_id: int, hours_ago: int) -> BrowseSession:
    now = utcnow_naive()
    db = SessionLocal()
    try:
        browse = BrowseSession(
            user_id=user_id,
            started_at=now - timedelta(hours=hours_ago + 1),
            last_seen_at=now - timedelta(hours=hours_ago),
        )
        db.add(browse)
        db.commit()
        db.refresh(browse)
        return browse
    finally:
        db.close()


def _add_session_events(user_id: int, browse_id: int, count: int = 3) -> None:
    db = SessionLocal()
    try:
        for i in range(count):
            db.add(
                UserEvent(
                    user_id=user_id,
                    session_id=None,
                    browse_session_id=browse_id,
                    event_type="product_view",
                    entity_type="product",
                    entity_id=str(i + 1),
                    payload={},
                )
            )
        db.commit()
    finally:
        db.close()


def _backdate(db, browse: BrowseSession, hours: float) -> None:
    row = db.get(BrowseSession, browse.id)
    row.last_seen_at = utcnow_naive() - timedelta(hours=hours)
    db.commit()


def test_touch_or_create_reuses_within_gap_creates_new_after_gap(client):
    db = SessionLocal()
    try:
        first = browse_service.touch_or_create(db, user_id=None, session_key="guest-abc")
        second = browse_service.touch_or_create(db, user_id=None, session_key="guest-abc")
        assert second.id == first.id

        # simulate inactivity longer than the gap -> new session
        row = db.get(BrowseSession, first.id)
        row.last_seen_at = utcnow_naive() - timedelta(minutes=60)
        db.commit()
        third = browse_service.touch_or_create(db, user_id=None, session_key="guest-abc")
        assert third.id != first.id
    finally:
        db.close()


def test_events_api_assigns_browse_session(client):
    _seed_catalog()
    user = _register_user(client)
    resp = client.post(
        "/api/events/batch",
        json={
            "events": [
                {"event_type": "product_view", "entity_type": "product", "entity_id": "1", "payload": {}}
            ]
        },
    )
    assert resp.status_code == 200

    db = SessionLocal()
    try:
        event = db.query(UserEvent).filter(UserEvent.user_id == user.id).first()
        assert event is not None
        assert event.browse_session_id is not None
        browse = db.get(BrowseSession, event.browse_session_id)
        assert browse is not None
        assert browse.user_id == user.id
    finally:
        db.close()


def test_session_digest_sends_1h_6h_12h_and_reuses_rec(client, vector_store, monkeypatch):
    _seed_catalog()
    user = _register_user(client)
    browse = _make_browse_session(user.id, hours_ago=2)
    _add_session_events(user.id, browse.id, count=3)

    sent: list[str] = []
    monkeypatch.setattr(digest_service, "send_email", lambda *a, **k: sent.append(a[1]) or True)
    monkeypatch.setattr(digest_service, "send_telegram", lambda *a, **k: True)

    db = SessionLocal()
    try:
        # 1h slot is due (2h elapsed)
        r1 = digest_service.run_session_digests(db)
        assert r1["slots_sent"] == 1
        runs_after_1h = db.query(AgentRun).filter(AgentRun.user_id == user.id).count()
        assert runs_after_1h == 1

        # 6h slot due
        _backdate(db, browse, 7)
        r2 = digest_service.run_session_digests(db)
        assert r2["slots_sent"] == 1
        assert db.query(AgentRun).filter(AgentRun.user_id == user.id).count() == runs_after_1h

        # 12h slot due
        _backdate(db, browse, 13)
        r3 = digest_service.run_session_digests(db)
        assert r3["slots_sent"] == 1

        rows = (
            db.query(SessionDigest)
            .filter(SessionDigest.browse_session_id == browse.id)
            .order_by(SessionDigest.sent_at)
            .all()
        )
        assert [r.slot for r in rows] == ["1h", "6h", "12h"]
        assert all(r.status == "sent" for r in rows)
    finally:
        db.close()

    assert len(sent) == 3
    assert any("Quick picks" in s for s in sent)
    assert any("Still on the fence" in s for s in sent)
    assert any("Last look" in s for s in sent)


def test_session_digest_skips_session_without_signal(client, vector_store, monkeypatch):
    user = _register_user(client)
    browse = _make_browse_session(user.id, hours_ago=2)
    db = SessionLocal()
    try:
        # no events at all -> not meaningful -> skipped, but recorded once
        r = digest_service.run_session_digests(db)
        assert r["slots_skipped"] == 1
        row = (
            db.query(SessionDigest)
            .filter(SessionDigest.browse_session_id == browse.id)
            .first()
        )
        assert row is not None
        assert row.status == "skipped"
        assert row.slot == "1h"

        # later slots also skip without sending
        _backdate(db, browse, 7)
        r2 = digest_service.run_session_digests(db)
        assert r2["slots_sent"] == 0
        rows = (
            db.query(SessionDigest)
            .filter(SessionDigest.browse_session_id == browse.id)
            .all()
        )
        assert [x.slot for x in rows] == ["1h", "6h"]
    finally:
        db.close()


def test_cron_session_digests_requires_secret(client):
    resp = client.get("/cron/sessions")
    assert resp.status_code == 403
    resp = client.get("/cron/sessions?token=wrong")
    assert resp.status_code == 403


def test_cron_session_digests_runs(client, vector_store, monkeypatch):
    from app.config import settings as app_settings

    _seed_catalog()
    user = _register_user(client)
    browse = _make_browse_session(user.id, hours_ago=2)
    _add_session_events(user.id, browse.id, count=3)
    monkeypatch.setattr(digest_service, "send_email", lambda *a, **k: True)

    resp = client.get(f"/cron/sessions?token={app_settings.cron_secret}")
    assert resp.status_code == 200
    assert resp.json()["slots_sent"] == 1
