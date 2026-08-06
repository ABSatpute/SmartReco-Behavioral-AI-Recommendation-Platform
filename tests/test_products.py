import hashlib

import pytest

from app.database import SessionLocal
from app.models import Product, User
from app.services import mesh as mesh_service
from app.services import products as product_service
from app.vector_store import InMemoryVectorStore


def fake_embed(texts, model=None):
    out = []
    for text in texts:
        h = hashlib.sha256(text.encode()).digest()
        out.append([((h[i] % 256) / 128) - 1 for i in range(8)])
    return out


@pytest.fixture()
def vector_store(monkeypatch):
    store = InMemoryVectorStore()
    monkeypatch.setattr(product_service, "get_vector_store", lambda: store)
    monkeypatch.setattr(mesh_service, "embed", fake_embed)
    monkeypatch.setattr(
        mesh_service, "embed_one", lambda text, model=None: fake_embed([text])[0]
    )
    return store


def _register_and_make_admin(client, email="admin@x.com"):
    client.post(
        "/auth/register",
        data={"email": email, "password": "adminpass1", "full_name": "Admin"},
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
        data={"email": email, "password": "userpass1", "full_name": "User"},
        follow_redirects=False,
    )


PRODUCT_FORM = {
    "title": "Intro to RAG",
    "category": "AI Engineering",
    "price": "49.00",
    "level": "beginner",
    "tags": "rag embeddings",
    "description": "Build retrieval-augmented generation from scratch.",
}


def test_admin_routes_require_admin(client):
    resp = client.get("/admin/products")
    assert resp.status_code in (401, 303)

    _register_regular(client)
    resp = client.get("/admin/products")
    assert resp.status_code == 403


def test_create_product_dual_write(client, vector_store):
    _register_and_make_admin(client)
    resp = client.post("/admin/products", data=PRODUCT_FORM, follow_redirects=False)
    assert resp.status_code == 303

    db = SessionLocal()
    product = db.query(Product).first()
    db.close()
    assert product is not None
    assert product.slug == "intro-to-rag"
    assert vector_store.count() == 1
    assert "product:1" in vector_store._vectors


def test_update_product_syncs_vector(client, vector_store):
    _register_and_make_admin(client)
    client.post("/admin/products", data=PRODUCT_FORM, follow_redirects=False)
    client.post(
        "/admin/products/1",
        data={**PRODUCT_FORM, "title": "Advanced RAG Systems", "category": "Data Science"},
        follow_redirects=False,
    )

    db = SessionLocal()
    product = db.get(Product, 1)
    db.close()
    assert product.title == "Advanced RAG Systems"
    assert vector_store._metadata["product:1"]["title"] == "Advanced RAG Systems"


def test_delete_product_removes_vector(client, vector_store):
    _register_and_make_admin(client)
    client.post("/admin/products", data=PRODUCT_FORM, follow_redirects=False)
    resp = client.post("/admin/products/1/delete", follow_redirects=False)
    assert resp.status_code == 303

    db = SessionLocal()
    product = db.get(Product, 1)
    db.close()
    assert product.is_active is False
    assert vector_store.count() == 0


def test_product_detail_page_tracks(client, vector_store):
    _register_and_make_admin(client)
    client.post("/admin/products", data=PRODUCT_FORM, follow_redirects=False)

    resp = client.get("/products/intro-to-rag")
    assert resp.status_code == 200
    assert "Intro to RAG" in resp.text


def test_product_detail_404(client):
    resp = client.get("/products/does-not-exist")
    assert resp.status_code == 404


def test_search_products(client, vector_store):
    _register_and_make_admin(client)
    client.post("/admin/products", data=PRODUCT_FORM, follow_redirects=False)

    db = SessionLocal()
    results = product_service.search_products(db, "rag")
    db.close()
    assert len(results) == 1
    assert results[0].title == "Intro to RAG"


def test_search_products_category_filter(client, vector_store):
    _register_and_make_admin(client)
    client.post("/admin/products", data=PRODUCT_FORM, follow_redirects=False)

    db = SessionLocal()
    results = product_service.search_products(db, "rag", category="Data Science")
    db.close()
    assert len(results) == 0  # category mismatch

    db = SessionLocal()
    results = product_service.search_products(db, "rag", category="AI Engineering")
    db.close()
    assert len(results) == 1


def test_browse_category_without_query(client, vector_store):
    _register_and_make_admin(client)
    client.post("/admin/products", data=PRODUCT_FORM, follow_redirects=False)
    client.post(
        "/admin/products",
        data={**PRODUCT_FORM, "title": "Data Pipelines", "category": "Data Engineering"},
        follow_redirects=False,
    )

    db = SessionLocal()
    results = product_service.products_by_category(db, "AI Engineering")
    db.close()
    assert [p.title for p in results] == ["Intro to RAG"]

    resp = client.get("/search?category=AI+Engineering")
    assert resp.status_code == 200
    assert "Intro to RAG" in resp.text
    assert "No products found" not in resp.text


def test_invalid_price_rejected(client, vector_store):
    _register_and_make_admin(client)
    resp = client.post(
        "/admin/products",
        data={**PRODUCT_FORM, "price": "not-a-number"},
    )
    assert resp.status_code == 400
    db = SessionLocal()
    assert db.query(Product).count() == 0
    db.close()
