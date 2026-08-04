from app.database import SessionLocal
from app.models import User


def test_register_success(client):
    resp = client.post(
        "/auth/register",
        data={"email": "jane@example.com", "password": "supersecret", "full_name": "Jane"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"
    assert "smartreco_session" in resp.headers["set-cookie"]

    db = SessionLocal()
    user = db.query(User).filter(User.email == "jane@example.com").first()
    db.close()
    assert user is not None
    assert user.role == "user"
    assert user.password_hash != "supersecret"


def test_register_duplicate_email(client):
    payload = {"email": "dup@example.com", "password": "supersecret"}
    assert client.post("/auth/register", data=payload, follow_redirects=False).status_code == 303
    assert client.post("/auth/register", data=payload).status_code == 400


def test_register_short_password(client):
    resp = client.post(
        "/auth/register", data={"email": "short@example.com", "password": "short"}
    )
    assert resp.status_code == 400


def test_login_success_and_session(client):
    client.post(
        "/auth/register",
        data={"email": "bob@example.com", "password": "supersecret"},
    )
    client.get("/auth/logout", follow_redirects=False)

    resp = client.post(
        "/auth/login",
        data={"email": "bob@example.com", "password": "supersecret"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "smartreco_session" in resp.headers["set-cookie"]


def test_login_wrong_password(client):
    client.post(
        "/auth/register",
        data={"email": "carol@example.com", "password": "supersecret"},
    )
    resp = client.post(
        "/auth/login",
        data={"email": "carol@example.com", "password": "wrongpassword"},
    )
    assert resp.status_code == 400


def test_recommendations_requires_login(client):
    resp = client.get("/recommendations", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/auth/login"


def test_home_renders(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "SmartReco" in resp.text
