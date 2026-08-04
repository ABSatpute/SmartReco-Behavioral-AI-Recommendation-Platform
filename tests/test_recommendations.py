import hashlib

import pytest

from app.agent import nodes
from app.database import SessionLocal
from app.models import AgentRun, Recommendation, User, UserEvent
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
HALLUCINATED_JSON = GENERATION_JSON.replace('"product_id": 1', '"product_id": 999')


def fake_embed(texts, model=None):
    out = []
    for text in texts:
        h = hashlib.sha256(text.encode()).digest()
        out.append([((h[i] % 256) / 128) - 1 for i in range(8)])
    return out


def make_chat(hallucinate=False):
    def fake_chat_meta(messages, model=None, **kwargs):
        system = messages[0]["content"]
        if "behavioral analyst" in system:
            return PROFILE_JSON, 42
        return (HALLUCINATED_JSON if hallucinate else GENERATION_JSON), 99

    return fake_chat_meta


@pytest.fixture()
def vector_store(monkeypatch):
    store = InMemoryVectorStore()
    monkeypatch.setattr(product_service, "get_vector_store", lambda: store)
    monkeypatch.setattr(nodes, "get_vector_store", lambda: store)
    monkeypatch.setattr(mesh_service, "embed", fake_embed)
    monkeypatch.setattr(
        mesh_service, "embed_one", lambda text, model=None: fake_embed([text])[0]
    )
    monkeypatch.setattr(mesh_service, "chat_meta", make_chat())
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
        data={"email": email, "password": "alicepass1", "full_name": "Alice"},
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


def test_trigger_policy_threshold(client):
    user = _register_user(client)
    db = SessionLocal()
    try:
        assert rec_service.should_run(db, user.id) is False
        # establish a run so a cooldown window exists
        db_ = SessionLocal()
        try:
            user2 = db_.query(User).filter(User.id == user.id).first()
            rec_service.run(db_, user2, source="auto")
        finally:
            db_.close()
        # within cooldown, fewer than 3 new events -> no rerun
        _add_events(user.id, count=2)
        assert rec_service.should_run(db, user.id) is False
        # 3 new meaningful events bypass the cooldown
        _add_events(user.id, count=3)
        assert rec_service.should_run(db, user.id) is True
    finally:
        db.close()


def test_trigger_policy_first_event_runs_and_cooldown_blocks(client):
    user = _register_user(client)
    db = SessionLocal()
    try:
        # no prior run: the first meaningful signal triggers a first evaluation
        _add_events(user.id, count=1)
        assert rec_service.should_run(db, user.id) is True
        rec_service.run(db, user, source="auto")
        assert rec_service.should_run(db, user.id) is False
    finally:
        db.close()


def test_agent_run_stores_recommendation_and_trace(client, vector_store):
    _seed_catalog()
    user = _register_user(client)
    _add_events(user.id, count=3)

    db = SessionLocal()
    try:
        rec = rec_service.run(db, user, source="auto")
        assert rec is not None
        assert rec.summary == "Your next step in RAG"
        assert len(rec.items) == 2
        assert [i.product_id for i in rec.items] == [1, 2]

        run = rec_service.last_run(db, user.id)
        assert run is not None
        assert run.trace_id
        assert run.llm_calls >= 2  # analyze + generate
        nodes_seen = {s["node"] for s in run.steps}
        assert {"analyze", "decide", "retrieve", "evaluate", "generate", "store"} <= nodes_seen
    finally:
        db.close()


def test_agent_grounding_rejects_hallucinated_ids(client, vector_store, monkeypatch):
    _seed_catalog()
    user = _register_user(client)
    _add_events(user.id, count=3)
    monkeypatch.setattr(mesh_service, "chat_meta", make_chat(hallucinate=True))

    db = SessionLocal()
    try:
        rec = rec_service.run(db, user, source="auto")
        assert rec is not None
        product_ids = {i.product_id for i in rec.items}
        assert product_ids and product_ids <= {1, 2, 3}
        run = rec_service.last_run(db, user.id)
        assert "fallback picks used" in " ".join(
            s.get("note", "") for s in run.steps
        )
    finally:
        db.close()


def test_serve_cached_does_not_rerun(client, vector_store):
    _seed_catalog()
    user = _register_user(client)
    _add_events(user.id, count=3)

    db = SessionLocal()
    try:
        first = rec_service.ensure(db, user)
        assert first is not None
        run_count = db.query(AgentRun).filter(AgentRun.user_id == user.id).count()
        second = rec_service.ensure(db, user)
        assert second.id == first.id
        assert (
            db.query(AgentRun).filter(AgentRun.user_id == user.id).count() == run_count
        )
    finally:
        db.close()


def test_refresh_forces_new_run(client, vector_store):
    _seed_catalog()
    user = _register_user(client)
    _add_events(user.id, count=3)

    db = SessionLocal()
    try:
        first = rec_service.refresh(db, user)
        run_count = db.query(AgentRun).filter(AgentRun.user_id == user.id).count()
        second = rec_service.refresh(db, user)
        assert db.query(AgentRun).filter(AgentRun.user_id == user.id).count() == run_count + 1
        assert second.id != first.id
    finally:
        db.close()


def test_recommendations_page_and_api(client, vector_store):
    _seed_catalog()
    _register_user(client)
    _add_events(1, count=3)

    resp = client.post("/recommendations/refresh", follow_redirects=False)
    assert resp.status_code == 303

    page = client.get("/recommendations")
    assert page.status_code == 200
    assert "Your next step in RAG" in page.text
    assert "Building RAG Systems" in page.text

    api = client.get("/api/recommendations/latest")
    assert api.status_code == 200
    data = api.json()
    assert data["summary"] == "Your next step in RAG"
    assert len(data["items"]) == 2


def test_recommendations_page_requires_login(client):
    resp = client.get("/recommendations", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/auth/login"
