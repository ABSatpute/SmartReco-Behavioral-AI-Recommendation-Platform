from app.database import SessionLocal
from app.models import User


def test_register_success(client):
    resp = client.post(
        "/auth/register",
        data={
            "email": "jane@example.com",
            "password": "supersecret",
            "full_name": "Jane",
            "mobile": "+91 90000 00001",
            "age": "28",
            "gender": "female",
        },
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
    payload = {
        "email": "dup@example.com",
        "password": "supersecret",
        "full_name": "Dup User",
        "mobile": "+91 90000 00002",
        "age": "30",
        "gender": "male",
    }
    assert client.post("/auth/register", data=payload, follow_redirects=False).status_code == 303
    assert client.post("/auth/register", data=payload).status_code == 400


def test_register_short_password(client):
    resp = client.post(
        "/auth/register",
        data={
            "email": "short@example.com",
            "password": "short",
            "full_name": "Short User",
            "mobile": "+91 90000 00003",
            "age": "25",
            "gender": "female",
        },
    )
    assert resp.status_code == 400


def test_login_success_and_session(client):
    client.post(
        "/auth/register",
        data={
            "email": "bob@example.com",
            "password": "supersecret",
            "full_name": "Bob",
            "mobile": "+91 90000 00004",
            "age": "32",
            "gender": "male",
        },
    )
    client.get("/auth/logout", follow_redirects=False)

    resp = client.post(
        "/auth/login",
        data={"email": "bob@example.com", "password": "supersecret"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "smartreco_session" in resp.headers["set-cookie"]
    assert "smartreco_flash" in resp.headers["set-cookie"]


def test_flash_set_on_register(client):
    resp = client.post(
        "/auth/register",
        data={
            "email": "flash@example.com",
            "password": "supersecret",
            "full_name": "Flash",
            "mobile": "+91 90000 00007",
            "age": "29",
            "gender": "male",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "smartreco_flash" in resp.headers["set-cookie"]


def test_flash_set_on_logout(client):
    client.post(
        "/auth/register",
        data={
            "email": "logout@example.com",
            "password": "supersecret",
            "full_name": "Logout",
            "mobile": "+91 90000 00008",
            "age": "31",
            "gender": "female",
        },
        follow_redirects=False,
    )
    resp = client.post("/auth/logout", follow_redirects=False)
    assert resp.status_code == 303
    assert "smartreco_flash" in resp.headers["set-cookie"]


def test_login_wrong_password(client):
    client.post(
        "/auth/register",
        data={
            "email": "carol@example.com",
            "password": "supersecret",
            "full_name": "Carol",
            "mobile": "+91 90000 00005",
            "age": "27",
            "gender": "female",
        },
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


def test_home_redirects_to_login_when_logged_out(client):
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/auth/login"


def test_home_renders_for_logged_in_user(client):
    client.post(
        "/auth/register",
        data={
            "email": "home@example.com",
            "password": "homepass1",
            "full_name": "Home User",
            "mobile": "+91 90000 00009",
            "age": "25",
            "gender": "other",
        },
        follow_redirects=False,
    )
    resp = client.get("/")
    assert resp.status_code == 200
    assert "SmartReco" in resp.text


def test_admin_home_redirects_to_dashboard(client):
    client.post(
        "/auth/register",
        data={
            "email": "adminhome@example.com",
            "password": "adminpass1",
            "full_name": "Admin Home",
            "mobile": "+91 90000 00010",
            "age": "35",
            "gender": "other",
        },
        follow_redirects=False,
    )
    from app.database import SessionLocal
    from app.models import User

    db = SessionLocal()
    user = db.query(User).filter(User.email == "adminhome@example.com").first()
    user.role = "admin"
    db.commit()
    db.close()
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/admin"
