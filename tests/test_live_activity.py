import time

from app.database import SessionLocal
from app.models import User, UserEvent


def _make_admin(client, email="liveadmin@x.com"):
    client.post(
        "/auth/register",
        data={
            "email": email,
            "password": "adminpass1",
            "full_name": "Live Admin",
            "mobile": "+91 90000 00021",
            "age": "38",
            "gender": "male",
        },
        follow_redirects=False,
    )
    db = SessionLocal()
    user = db.query(User).filter(User.email == email).first()
    user.role = "admin"
    db.commit()
    db.close()


def _login(client, email, password):
    return client.post(
        "/auth/login", data={"email": email, "password": password}, follow_redirects=False
    )


def _add_event(user_id, event_type, entity, entity_id):
    db = SessionLocal()
    try:
        db.add(
            UserEvent(
                user_id=user_id,
                event_type=event_type,
                entity_type=entity,
                entity_id=entity_id,
                payload={"probe": 1},
            )
        )
        db.commit()
    finally:
        db.close()


def test_live_page_requires_admin(client):
    resp = client.get("/admin/live")
    assert resp.status_code in (401, 303)


def test_live_page_renders_recent_events(client):
    _make_admin(client)
    _make_admin(client, email="liveadmin2@x.com")
    admin = SessionLocal().query(User).filter(User.email == "liveadmin@x.com").first()
    _add_event(admin.id, "search", "query", "rag")
    _add_event(admin.id, "product_view", "product", "7")

    _login(client, "liveadmin2@x.com", "adminpass1")
    resp = client.get("/admin/live")
    assert resp.status_code == 200
    assert "Live Activity" in resp.text
    assert "rag" in resp.text
    assert "product_view" in resp.text


def test_live_recent_json(client):
    _make_admin(client)
    _login(client, "liveadmin@x.com", "adminpass1")
    resp = client.get("/admin/events/recent", params={"limit": 5})
    assert resp.status_code == 200
    data = resp.json()
    assert "events" in data and "last_id" in data
    assert isinstance(data["events"], list)
    assert data["last_id"] >= 0


def test_live_stream_requires_admin(client):
    with client.stream("GET", "/admin/events/stream", params={"after": 0}) as resp:
        pass
    assert resp.status_code in (401, 403)


def test_live_stream_pushes_new_events():
    """Drive the SSE generator directly (no HTTP round-trip) and assert it
    emits a data frame for an event that lands after it connects."""
    import asyncio

    db = SessionLocal()
    try:
        admin = User(
            email="liveadmin@x.com",
            password_hash="x",
            full_name="Live Admin",
            mobile="+91 90000 00021",
            age=38,
            gender="male",
            role="admin",
        )
        db.add(admin)
        db.commit()
        db.refresh(admin)
        user_id = admin.id
    finally:
        db.close()

    async def run():
        from app.router.admin import _event_stream

        db = SessionLocal()
        try:
            async def always_connected() -> bool:
                return False

            gen = _event_stream(db, after=0, is_disconnected=always_connected)
            first = await anext(gen)
            assert first.strip() == "retry: 3000"

            _add_event(user_id, "add_to_cart", "product", "99")

            deadline = time.time() + 8
            frames = []
            while time.time() < deadline:
                chunk = await anext(gen)
                frames.append(chunk)
                if "add_to_cart" in chunk:
                    break
                await asyncio.sleep(0.1)
            await gen.aclose()
            return "".join(frames)
        finally:
            db.close()

    joined = asyncio.run(run())
    assert "add_to_cart" in joined
    assert '"probe"' in joined
