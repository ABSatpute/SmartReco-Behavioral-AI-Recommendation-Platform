import hashlib

import pytest

from app.cli import clean_amazon_row
from app.database import SessionLocal
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


def test_clean_amazon_row_valid():
    row = clean_amazon_row(
        {
            "asin": "B00ABC123",
            "title": "LED Desk Lamp",
            "imgUrl": "https://m.media-amazon.com/images/I/1.jpg",
            "productURL": "https://www.amazon.co.uk/dp/B00ABC123",
            "stars": "4.5",
            "reviews": "231",
            "price": "24.99",
            "isBestSeller": "True",
            "boughtInLastMonth": "1234",
            "categoryName": "Home & Kitchen > Lighting > Lamps",
        }
    )
    assert row["asin"] == "B00ABC123"
    assert row["price"] == 24.99
    assert row["stars"] == 4.5
    assert row["reviews"] == 231
    assert row["is_best_seller"] is True
    assert row["bought_in_last_month"] == 1234
    assert row["image_url"].startswith("http")
    assert "lighting" in row["tags"]


def test_clean_amazon_row_rejects_dirty_data():
    assert clean_amazon_row({"title": "", "categoryName": "X", "price": "5"}) is None
    assert clean_amazon_row({"title": "T", "categoryName": "C", "price": "not-a-number"}) is None
    assert clean_amazon_row({"title": "T", "categoryName": "C", "price": "-1"}) is None
    assert clean_amazon_row({"title": "T", "categoryName": "C", "price": "99999999"}) is None


def test_clean_amazon_row_coerces_bad_numbers():
    row = clean_amazon_row(
        {
            "asin": "B1",
            "title": "T",
            "categoryName": "C",
            "price": "9.99",
            "stars": "abc",
            "reviews": "12",
            "isBestSeller": "false",
            "boughtInLastMonth": "0",
            "imgUrl": "",
            "productURL": "",
        }
    )
    assert row["stars"] is None
    assert row["reviews"] == 12
    assert row["is_best_seller"] is False


def test_bulk_create_and_embed(vector_store):
    db = SessionLocal()
    rows = [
        {"asin": f"A{i}", "title": f"Product {i}", "category": "Gadgets", "price": 10 + i,
         "stars": 4.0, "reviews": 5, "is_best_seller": False, "tags": ["gadget"]}
        for i in range(1, 4)
    ]
    ids = product_service.bulk_create_products(db, rows)
    assert len(ids) == 3

    embedded = product_service.embed_product_batch(db, ids)
    assert embedded == 3
    assert vector_store.count() == 3
    assert vector_store._metadata["product:1"]["category"] == "Gadgets"

    db.close()


def test_bulk_create_dedupes_asin(vector_store):
    db = SessionLocal()
    rows = [
        {"asin": "A1", "title": "Dup", "category": "C", "price": 1, "tags": []},
        {"asin": "A1", "title": "Dup again", "category": "C", "price": 2, "tags": []},
        {"asin": "A2", "title": "New", "category": "C", "price": 3, "tags": []},
    ]
    ids = product_service.bulk_create_products(db, rows)
    assert len(ids) == 2
    db.close()
