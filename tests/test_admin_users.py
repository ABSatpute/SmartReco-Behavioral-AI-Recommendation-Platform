import pytest

from app.database import SessionLocal
from app.models import User


def _make_admin(client, email="boss@x.com"):
    client.post(
        "/auth/register",
        data={
            "email": email,
            "password": "adminpass1",
            "full_name": "Boss Admin",
            "mobile": "+91 90000 00001",
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


def _make_user(client, email="worker@x.com"):
    client.post(
        "/auth/register",
        data={
            "email": email,
            "password": "userpass1",
            "full_name": "Worker User",
            "mobile": "+91 90000 00002",
            "age": "30",
            "gender": "female",
        },
        follow_redirects=False,
    )


def _login(client, email, password):
    return client.post(
        "/auth/login", data={"email": email, "password": password}, follow_redirects=False
    )


def test_users_page_requires_admin(client):
    resp = client.get("/admin/users")
    assert resp.status_code in (401, 303)


def test_users_list_and_edit_flow(client):
    _make_admin(client)
    _make_user(client)
    _login(client, "boss@x.com", "adminpass1")

    db = SessionLocal()
    worker = db.query(User).filter(User.email == "worker@x.com").first()
    worker_id = worker.id
    db.close()

    resp = client.get("/admin/users")
    assert resp.status_code == 200
    assert "worker@x.com" in resp.text
    assert "Boss Admin" in resp.text

    resp = client.get(f"/admin/users/{worker_id}/edit")
    assert resp.status_code == 200
    assert "worker@x.com" in resp.text

    # Update: change name, age, role, telegram, add password
    resp = client.post(
        f"/admin/users/{worker_id}",
        data={
            "full_name": "Worker Updated",
            "email": "worker@x.com",
            "mobile": "+91 90000 00002",
            "age": "31",
            "gender": "female",
            "role": "admin",
            "telegram_chat_id": "123456789",
            "new_password": "newpass123",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin/users"

    db = SessionLocal()
    worker = db.query(User).filter(User.id == worker_id).first()
    assert worker.full_name == "Worker Updated"
    assert worker.age == 31
    assert worker.role == "admin"
    assert worker.telegram_chat_id == "123456789"
    assert worker.password_hash != ""
    db.close()


def test_users_edit_validation(client):
    _make_admin(client)
    _make_user(client)
    _login(client, "boss@x.com", "adminpass1")

    db = SessionLocal()
    worker_id = db.query(User).filter(User.email == "worker@x.com").first().id
    db.close()

    # empty required fields should 400 with repopulation
    resp = client.post(
        f"/admin/users/{worker_id}",
        data={"full_name": "", "email": "bad", "mobile": "x", "age": "999",
              "gender": "", "role": "user", "telegram_chat_id": "", "new_password": "short"},
    )
    assert resp.status_code == 400
    assert "Another account" not in resp.text  # no false uniqueness error yet
    assert 'value="bad"' in resp.text  # repopulated email


def test_admin_can_create_user(client):
    _make_admin(client)

    resp = client.get("/admin/users/new")
    assert resp.status_code == 200

    resp = client.post(
        "/admin/users",
        data={
            "full_name": "New User",
            "email": "new@x.com",
            "mobile": "+91 90000 00003",
            "age": "27",
            "gender": "other",
            "role": "user",
            "telegram_chat_id": "",
            "new_password": "newpass123",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin/users"

    db = SessionLocal()
    user = db.query(User).filter(User.email == "new@x.com").first()
    assert user is not None
    assert user.full_name == "New User"
    assert user.role == "user"
    assert user.age == 27
    db.close()

    # created user can log in with the set password
    _login(client, "new@x.com", "newpass123")
    resp = client.get("/")
    assert resp.status_code == 200


def test_admin_cannot_create_duplicate_email(client):
    _make_admin(client)
    _make_user(client)
    _login(client, "boss@x.com", "adminpass1")

    resp = client.post(
        "/admin/users",
        data={
            "full_name": "Dup User",
            "email": "worker@x.com",
            "mobile": "+91 90000 00004",
            "age": "28",
            "gender": "male",
            "role": "user",
            "telegram_chat_id": "",
            "new_password": "newpass123",
        },
    )
    assert resp.status_code == 400
    assert "already exists" in resp.text


def test_admin_cannot_create_user_without_password(client):
    _make_admin(client)

    resp = client.post(
        "/admin/users",
        data={
            "full_name": "No Pass",
            "email": "nopass@x.com",
            "mobile": "+91 90000 00005",
            "age": "28",
            "gender": "male",
            "role": "user",
            "telegram_chat_id": "",
            "new_password": "",
        },
    )
    assert resp.status_code == 400
    assert "Password is required" in resp.text


def test_users_email_uniqueness_excludes_self(client):
    _make_admin(client, email="admin@x.com")

    db = SessionLocal()
    admin_id = db.query(User).filter(User.email == "admin@x.com").first().id
    db.close()

    resp = client.post(
        f"/admin/users/{admin_id}",
        data={
            "full_name": "Boss Admin",
            "email": "admin@x.com",
            "mobile": "+91 90000 00001",
            "age": "40",
            "gender": "male",
            "role": "admin",
            "telegram_chat_id": "",
            "new_password": "",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303


def test_users_delete_blocks_self_and_allows_others(client):
    _make_admin(client)
    _make_user(client)
    _login(client, "boss@x.com", "adminpass1")

    db = SessionLocal()
    admin_id = db.query(User).filter(User.email == "boss@x.com").first().id
    worker_id = db.query(User).filter(User.email == "worker@x.com").first().id
    db.close()

    # deleting yourself is blocked
    resp = client.post(f"/admin/users/{admin_id}/delete")
    assert resp.status_code == 400

    resp = client.post(f"/admin/users/{worker_id}/delete", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin/users"

    db = SessionLocal()
    assert db.query(User).filter(User.id == worker_id).first() is None
    db.close()
