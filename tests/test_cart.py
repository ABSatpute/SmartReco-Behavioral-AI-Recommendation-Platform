from app.database import SessionLocal
from app.models import CartItem, Product, User


def _add_product(title="Test Widget", price=19.99):
    db = SessionLocal()
    product = Product(
        title=title,
        slug=title.lower().replace(" ", "-"),
        category="Gadgets",
        price=price,
        tags=[],
        is_active=True,
    )
    db.add(product)
    db.commit()
    db.refresh(product)
    pid = product.id
    db.close()
    return pid


def _register(client, email="cart@example.com"):
    client.post(
        "/auth/register",
        data={
            "email": email,
            "password": "cartpass1",
            "full_name": "Cart",
            "mobile": "+91 90000 00011",
            "age": "25",
            "gender": "female",
        },
        follow_redirects=False,
    )


def test_guest_add_to_cart(client):
    pid = _add_product()
    resp = client.post("/api/cart/add", json={"product_id": pid, "quantity": 1})
    assert resp.status_code == 200
    assert resp.json()["count"] == 1

    page = client.get("/cart")
    assert page.status_code == 200
    assert "Test Widget" in page.text


def test_add_increments_quantity(client):
    pid = _add_product()
    client.post("/api/cart/add", json={"product_id": pid})
    resp = client.post("/api/cart/add", json={"product_id": pid, "quantity": 2})
    assert resp.json()["count"] == 3

    db = SessionLocal()
    row = db.query(CartItem).filter(CartItem.product_id == pid).first()
    db.close()
    assert row.quantity == 3


def test_update_and_remove(client):
    pid = _add_product()
    client.post("/api/cart/add", json={"product_id": pid})

    resp = client.post("/api/cart/update", json={"product_id": pid, "quantity": 5})
    assert resp.json()["count"] == 5

    resp = client.post("/api/cart/remove", json={"product_id": pid})
    assert resp.json()["count"] == 0

    page = client.get("/cart")
    assert "Your cart is empty" in page.text


def test_add_missing_product_errors(client):
    resp = client.post("/api/cart/add", json={"product_id": 99999})
    assert resp.status_code == 200
    assert resp.json()["ok"] is False


def test_cart_summary_badge(client):
    pid = _add_product()
    client.post("/api/cart/add", json={"product_id": pid})
    resp = client.get("/api/cart")
    assert resp.json()["count"] == 1
    assert resp.json()["subtotal"] == 19.99


def test_guest_cart_merges_on_register(client):
    pid = _add_product()
    client.post("/api/cart/add", json={"product_id": pid})
    _register(client)

    db = SessionLocal()
    user = db.query(User).filter(User.email == "cart@example.com").first()
    rows = db.query(CartItem).filter(CartItem.user_id == user.id).all()
    db.close()
    assert len(rows) == 1
    assert rows[0].product_id == pid
    assert rows[0].session_key is None


def test_guest_cart_merges_on_login(client):
    pid = _add_product()
    client.post("/api/cart/add", json={"product_id": pid})
    _register(client, email="login@example.com")
    client.post("/auth/logout")

    client.post("/api/cart/add", json={"product_id": pid})
    client.post("/auth/login", data={"email": "login@example.com", "password": "cartpass1"})

    db = SessionLocal()
    user = db.query(User).filter(User.email == "login@example.com").first()
    rows = db.query(CartItem).filter(CartItem.user_id == user.id).all()
    db.close()
    assert len(rows) == 1
    assert rows[0].quantity == 2


def test_logged_in_cart_keyed_by_user(client):
    pid = _add_product()
    _register(client)
    client.post("/api/cart/add", json={"product_id": pid})

    db = SessionLocal()
    user = db.query(User).filter(User.email == "cart@example.com").first()
    rows = db.query(CartItem).filter(CartItem.user_id == user.id).all()
    db.close()
    assert len(rows) == 1


def test_checkout_returns_summary(client):
    pid = _add_product()
    client.post("/api/cart/add", json={"product_id": pid})
    resp = client.post("/api/cart/checkout")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert resp.json()["subtotal"] == 19.99
    assert resp.json()["count"] == 1


def test_cart_page_works_for_guest(client):
    resp = client.get("/cart")
    assert resp.status_code == 200
