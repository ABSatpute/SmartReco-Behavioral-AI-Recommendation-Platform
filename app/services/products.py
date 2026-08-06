"""Product service: SQL CRUD + Pinecone dual-write.

SQL is the source of truth; the vector store is derived and kept in sync. Vector
writes are best-effort (failures are logged and recoverable via resync_vectors).
"""
import logging
import re
import time

from sqlalchemy.orm import Session

from app.models import Product
from app.services import mesh
from app.utils import utcnow_naive
from app.vector_store import get_vector_store

logger = logging.getLogger(__name__)


def slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug or "product"


def _unique_slug(db: Session, title: str, exclude_id: int | None = None) -> str:
    base = slugify(title)
    candidate = base
    n = 2
    while True:
        q = db.query(Product).filter(Product.slug == candidate)
        if exclude_id is not None:
            q = q.filter(Product.id != exclude_id)
        if q.first() is None:
            return candidate
        candidate = f"{base}-{n}"
        n += 1


def _product_text(product: Product) -> str:
    parts = [product.title, product.category, " ".join(product.tags or [])]
    if product.description:
        parts.append(product.description)
    return " ".join(p for p in parts if p).lower()[:8000]


def _vector_metadata(product: Product) -> dict:
    return {
        "title": product.title,
        "category": product.category,
        "tags": product.tags or [],
        "price": float(product.price),
        "level": product.level or "",
        "is_best_seller": bool(product.is_best_seller),
        "stars": float(product.stars) if product.stars is not None else 0.0,
        "reviews": int(product.reviews) if product.reviews is not None else 0,
        "bought_in_last_month": int(product.bought_in_last_month)
        if product.bought_in_last_month is not None
        else 0,
        "is_active": product.is_active,
    }


def _embed_products(db: Session, products: list[Product]) -> dict[int, list[float]]:
    """Embed many products in one Mesh call (batched)."""
    texts = [_product_text(p) for p in products]
    vectors = mesh.embed(texts)
    return {p.id: vec for p, vec in zip(products, vectors)}


def _embed_chunk_resilient(db: Session, chunk: list[Product], attempts: int = 3) -> dict[int, list[float]]:
    """Embed one chunk with bounded retries on transient Mesh errors."""
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return _embed_products(db, chunk)
        except mesh.MeshError as exc:
            last = exc
            logger.warning("Embed chunk failed (attempt %s/%s): %s", attempt, attempts, exc)
            if attempt < attempts:
                time.sleep(2 * attempt)
    raise last  # type: ignore[misc]


def _upsert_chunk_resilient(store, payload: list, attempts: int = 3) -> int:
    """Upsert one chunk with bounded retries on transient Pinecone failures."""
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            store.upsert(payload)
            return len(payload)
        except Exception as exc:  # noqa: BLE001 - Pinecone base except wraps network/timeout
            last = exc
            logger.warning("Vector upsert failed (attempt %s/%s): %s", attempt, attempts, exc)
            if attempt < attempts:
                time.sleep(2 * attempt)
    logger.error("Dropping %s vectors after %s failed attempts", len(payload), attempts)
    return 0


def _sync_products(db: Session, products: list[Product]) -> int:
    """Embed + upsert a product list in resilient 50-item chunks. Returns count."""
    store = get_vector_store()
    embedded = 0
    for i in range(0, len(products), 50):
        chunk = products[i : i + 50]
        try:
            vectors_by_id = _embed_chunk_resilient(db, chunk)
        except Exception as exc:  # noqa: BLE001 - never let one chunk kill a full backfill
            logger.error("Skipping chunk of %s products after embed failures: %s", len(chunk), exc)
            continue
        payload = []
        for p in chunk:
            vector = vectors_by_id.get(p.id)
            if vector is None:
                continue
            payload.append((f"product:{p.id}", vector, _vector_metadata(p)))
        if payload:
            embedded += _upsert_chunk_resilient(store, payload)
    return embedded


def sync_product_vector(db: Session, product: Product) -> None:
    """Dual-write: push (or remove) a single product's vector."""
    store = get_vector_store()
    vector_id = f"product:{product.id}"
    if not product.is_active:
        store.delete([vector_id])
        return
    try:
        vector = mesh.embed_one(_product_text(product))
        store.upsert([(vector_id, vector, _vector_metadata(product))])
    except mesh.MeshError as exc:
        logger.error("Vector sync failed for product %s: %s", product.id, exc)


def create_product(db: Session, data: dict) -> Product:
    product = Product(
        title=data["title"],
        slug=_unique_slug(db, data["title"]),
        description=data.get("description", ""),
        category=data.get("category", ""),
        tags=data.get("tags", []),
        price=float(data.get("price", 0)),
        level=data.get("level") or None,
        image_url=data.get("image_url") or None,
        product_url=data.get("product_url") or None,
        asin=data.get("asin") or None,
        stars=data.get("stars"),
        reviews=data.get("reviews"),
        is_best_seller=bool(data.get("is_best_seller", False)),
        bought_in_last_month=data.get("bought_in_last_month"),
    )
    db.add(product)
    db.commit()
    db.refresh(product)
    sync_product_vector(db, product)
    return product


def update_product(db: Session, product: Product, data: dict) -> Product:
    product.title = data["title"]
    product.description = data.get("description", "")
    product.category = data.get("category", "")
    product.tags = data.get("tags", [])
    product.price = float(data.get("price", 0))
    product.level = data.get("level") or None
    product.image_url = data.get("image_url") or None
    product.updated_at = utcnow_naive()
    db.commit()
    db.refresh(product)
    sync_product_vector(db, product)
    return product


def delete_product(db: Session, product: Product) -> None:
    """Soft delete: hide from catalog and remove its vector."""
    product.is_active = False
    product.updated_at = utcnow_naive()
    db.commit()
    sync_product_vector(db, product)


def hard_delete_product(db: Session, product: Product) -> None:
    get_vector_store().delete([f"product:{product.id}"])
    db.delete(product)
    db.commit()


def get_product_by_slug(db: Session, slug: str) -> Product | None:
    return (
        db.query(Product)
        .filter(Product.slug == slug, Product.is_active.is_(True))
        .first()
    )


def get_product_by_id(db: Session, product_id: int) -> Product | None:
    return db.query(Product).filter(Product.id == product_id).first()


def list_products(db: Session, include_inactive: bool = False) -> list[Product]:
    q = db.query(Product)
    if not include_inactive:
        q = q.filter(Product.is_active.is_(True))
    return q.order_by(Product.created_at.desc()).all()


def search_products(db: Session, query: str, category: str | None = None) -> list[Product]:
    """Catalog-grounded keyword search (used by the search page and as the
    retrieval fallback when embeddings are unavailable)."""
    q = query.strip()
    if not q:
        return []
    like = f"%{q}%"
    base = db.query(Product).filter(
        Product.is_active.is_(True),
        (Product.title.ilike(like))
        | (Product.description.ilike(like))
        | (Product.category.ilike(like)),
    )
    if category:
        base = base.filter(Product.category.ilike(f"%{category.strip()}%"))
    return base.limit(20).all()


def top_categories(db: Session, limit: int = 10) -> list[str]:
    """Most-populated active categories, for the storefront sub-nav."""
    from sqlalchemy import func

    rows = (
        db.query(Product.category)
        .filter(Product.is_active.is_(True))
        .group_by(Product.category)
        .order_by(func.count(Product.id).desc())
        .limit(limit)
        .all()
    )
    return [r[0] for r in rows]


def resync_all(db: Session) -> int:
    """Re-embed every active product into the vector store. Returns count."""
    products = list_products(db)
    if not products:
        return 0
    return _sync_products(db, products)


def bulk_create_products(db: Session, rows: list[dict]) -> list[int]:
    """Insert many products without per-item embedding (returns new ids).
    Vector sync happens later via embed_product_batch to keep Mesh calls cheap."""
    existing_asins = {
        a for (a,) in db.query(Product.asin).filter(Product.asin.isnot(None)).all()
    }
    existing_slugs = {s for (s,) in db.query(Product.slug).all()}

    products: list[Product] = []
    for row in rows:
        asin = row.get("asin")
        if asin and asin in existing_asins:
            continue
        if asin:
            existing_asins.add(asin)

        slug = slugify(row["title"])
        if slug in existing_slugs:
            base, n = slug, 2
            while f"{base}-{n}" in existing_slugs:
                n += 1
            slug = f"{base}-{n}"
        existing_slugs.add(slug)

        products.append(
            Product(
                title=row["title"],
                slug=slug,
                description=row.get("description", ""),
                category=row.get("category", ""),
                tags=row.get("tags", []),
                price=float(row.get("price", 0)),
                level=row.get("level") or None,
                image_url=row.get("image_url") or None,
                product_url=row.get("product_url") or None,
                asin=asin,
                stars=row.get("stars"),
                reviews=row.get("reviews"),
                is_best_seller=bool(row.get("is_best_seller", False)),
                bought_in_last_month=row.get("bought_in_last_month"),
            )
        )
        db.add(products[-1])
        if len(products) % 500 == 0:
            db.flush()

    db.commit()
    return [p.id for p in products]


def embed_product_batch(db: Session, product_ids: list[int]) -> int:
    """Embed a set of products in batched Mesh calls and upsert to the store."""
    if not product_ids:
        return 0
    products = (
        db.query(Product).filter(Product.id.in_(product_ids), Product.is_active.is_(True)).all()
    )
    if not products:
        return 0
    return _sync_products(db, products)
