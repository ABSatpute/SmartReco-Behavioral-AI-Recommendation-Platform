import hashlib

import pytest

from app.agent import nodes
from app.database import SessionLocal
from app.models import AgentRun, EmailDigest, User, UserEvent
from app.services import digest as digest_service
from app.services import mesh as mesh_service
from app.services import products as product_service
from app.services import recommendations as rec_service
from app.vector_store import InMemoryVectorStore

PROFILE_JSON = (
    '{"themes": ["AI engineering", "RAG"], '
    '"keywords": ["rag", "llm", "vector", "search", "retrieval"], '
    '"engagement": "high", "urgency": ["active search"], '
    '"search_intents": ["learn retrieval augmented generation"]}'
)
GENERATION_JSON = (
    '{"summary": "Your next step in RAG", '
    '"narrative": "You have been exploring retrieval and search. These picks will level you up.", '
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


def _register_user(client, email="alice@x.com"):
    client.post(
        "/auth/register",
        data={
            "email": email,
            "password": "alicepass1",
            "full_name": "Alice",
            "mobile": "+91 90000 00011",
            "age": "29",
            "gender": "female",
        },
        follow_redirects=False,
    )
    db = SessionLocal()
    user = db.query(User).filter(User.email == email).first()
    db.close()
    return user


def _add_events(user_id: int, count: int = 3, etype: str = "product_view") -> None:
    db = SessionLocal()
    try:
        for i in range(count):
            db.add(
                UserEvent(
                    user_id=user_id,
                    session_id=None,
                    event_type=etype,
                    entity_type="product",
                    entity_id=str(i + 1),
                    payload={},
                )
            )
        db.commit()
    finally:
        db.close()


def test_digest_sends_to_active_user_only(client, vector_store, monkeypatch):
    _seed_catalog()
    active = _register_user(client)
    inactive = _register_user(client, email="bob@x.com")
    _add_events(active.id, count=3)

    sent: list[tuple] = []
    monkeypatch.setattr(
        digest_service, "send_email", lambda to, subj, html, text: sent.append((to, subj)) or True
    )

    summary = digest_service.run_digest()
    assert summary["candidates"] == 1
    assert summary["sent"] == 1
    assert summary["no_recommendation"] == 0

    db = SessionLocal()
    try:
        digest = db.query(EmailDigest).filter(EmailDigest.user_id == active.id).first()
        assert digest is not None
        assert digest.status == "sent"
        assert digest.sent_at is not None
        assert db.query(EmailDigest).filter(EmailDigest.user_id == inactive.id).first() is None
    finally:
        db.close()

    assert sent and sent[0][0] == active.email
    assert "Your next step in RAG" in sent[0][1]


def test_digest_idempotent_one_per_day(client, vector_store, monkeypatch):
    _seed_catalog()
    user = _register_user(client)
    _add_events(user.id, count=3)
    monkeypatch.setattr(digest_service, "send_email", lambda *args: True)

    first = digest_service.run_digest()
    second = digest_service.run_digest()
    assert first["sent"] == 1
    assert second["sent"] == 0
    assert second["skipped_already"] == 1

    db = SessionLocal()
    try:
        count = db.query(EmailDigest).filter(EmailDigest.user_id == user.id).count()
        assert count == 1
    finally:
        db.close()


def test_digest_reuses_cached_recommendation(client, vector_store, monkeypatch):
    _seed_catalog()
    user = _register_user(client)
    _add_events(user.id, count=3)
    monkeypatch.setattr(digest_service, "send_email", lambda *args: True)

    db = SessionLocal()
    try:
        rec_service.run(db, user, source="manual", trigger="manual", force=True)
        runs_before = db.query(AgentRun).filter(AgentRun.user_id == user.id).count()
    finally:
        db.close()

    digest_service.run_digest()

    db = SessionLocal()
    try:
        runs_after = db.query(AgentRun).filter(AgentRun.user_id == user.id).count()
        assert runs_after == runs_before
    finally:
        db.close()


def test_digest_failed_send_is_retried(client, vector_store, monkeypatch):
    _seed_catalog()
    user = _register_user(client)
    _add_events(user.id, count=3)
    monkeypatch.setattr(digest_service, "send_email", lambda *args: False)

    first = digest_service.run_digest()
    assert first["failed"] == 1

    db = SessionLocal()
    try:
        row = db.query(EmailDigest).filter(EmailDigest.user_id == user.id).first()
        assert row.status == "failed"
    finally:
        db.close()

    monkeypatch.setattr(digest_service, "send_email", lambda *args: True)
    second = digest_service.run_digest()
    assert second["sent"] == 1

    db = SessionLocal()
    try:
        rows = db.query(EmailDigest).filter(EmailDigest.user_id == user.id).all()
        assert [r.status for r in rows] == ["failed", "sent"]
    finally:
        db.close()


def test_admin_digest_test_endpoint(client, vector_store):
    _seed_catalog()
    client.post(
        "/auth/register",
        data={
            "email": "admin@x.com",
            "password": "adminpass1",
            "full_name": "Admin",
            "mobile": "+91 90000 00012",
            "age": "35",
            "gender": "male",
        },
        follow_redirects=False,
    )
    db = SessionLocal()
    try:
        admin = db.query(User).filter(User.email == "admin@x.com").first()
        admin.role = "admin"
        db.commit()
        admin_id = admin.id
    finally:
        db.close()
    _add_events(admin_id, count=3)

    resp = client.post("/api/digest/test")
    assert resp.status_code == 200
    data = resp.json()
    assert data["candidates"] >= 1
    assert data["sent"] >= 1

    db = SessionLocal()
    try:
        assert db.query(EmailDigest).filter(EmailDigest.user_id == admin_id).count() == 1
    finally:
        db.close()
