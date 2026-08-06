"""CLI helpers: python -m app.cli <command>

Commands:
  create_admin --email E --password P         create an admin user
  seed_demo                                   seed demo products + demo admin
  load_amazon --csv PATH [--limit N] [--category S] [--embed]
                                              ingest Amazon UK 2023 dataset CSV
  resync_vectors                              re-embed all active products to Pinecone
  check_vectors                               report vector store health
  set_telegram --email E --chat-id ID         attach a Telegram chat id to a user
  telegram_chats [--email E]                   list chats seen by the bot
  test_email --to ADDR [--message MSG]        send a test email to verify the sender
"""
import argparse
import csv
import sys

from app.database import Base, SessionLocal, engine
from app.models import Product, User
from app.services import products as product_service
from app.services import auth as auth_service


def create_admin(email: str, password: str, full_name: str = "Admin") -> None:
    db = SessionLocal()
    try:
        if auth_service.get_user_by_email(db, email):
            print(f"User {email} already exists.")
            return
        user = User(
            email=email.lower().strip(),
            password_hash=auth_service.hash_password(password),
            full_name=full_name,
            role="admin",
        )
        db.add(user)
        db.commit()
        print(f"Admin created: {email}")
    finally:
        db.close()


DEMO_PRODUCTS = [
    {
        "title": "Build Agentic AI Systems with LangGraph",
        "category": "AI Engineering",
        "tags": ["agentic-ai", "langgraph", "rag"],
        "price": 89.0,
        "level": "advanced",
        "description": "Design multi-node reasoning agents: analyze user intent, retrieve from a vector store, evaluate retrieval quality, refine queries, and generate grounded answers. Hands-on projects building a full recommendation agent.",
    },
    {
        "title": "RAG from Scratch: Retrieval-Augmented Generation",
        "category": "AI Engineering",
        "tags": ["rag", "embeddings", "vector-db"],
        "price": 79.0,
        "level": "intermediate",
        "description": "Understand embeddings, chunking, vector search and metadata filtering. Build a grounded Q&A system over your own documents using Pinecone-style semantic retrieval.",
    },
    {
        "title": "Behavioral Product Analytics for Engineers",
        "category": "Data Science",
        "tags": ["analytics", "events", "product"],
        "price": 69.0,
        "level": "beginner",
        "description": "Track and reason over user behavior: event schemas, session identity, batching and throttling. Turn raw clicks and searches into signals that drive personalization.",
    },
    {
        "title": "Persuasive AI Copywriting with LLMs",
        "category": "AI Engineering",
        "tags": ["llm", "copywriting", "prompting"],
        "price": 59.0,
        "level": "beginner",
        "description": "Prompting techniques to generate motivating, personalized marketing copy that reflects a user's actual interests and journey, with calls to action that convert.",
    },
    {
        "title": "PostgreSQL Performance Masterclass",
        "category": "Backend",
        "tags": ["postgres", "sql", "indexing"],
        "price": 49.0,
        "level": "intermediate",
        "description": "Indexing strategies, query planning, full-text search with tsvector, and JSONB patterns for event-driven applications.",
    },
    {
        "title": "FastAPI in Production",
        "category": "Backend",
        "tags": ["fastapi", "async", "deployment"],
        "price": 99.0,
        "level": "advanced",
        "description": "Background schedulers, structured logging, test-driven API development, and deployment patterns for data-heavy Python services.",
    },
    {
        "title": "Frontend Observability & Event Tracking",
        "category": "Frontend",
        "tags": ["javascript", "tracking", "web"],
        "price": 45.0,
        "level": "beginner",
        "description": "Build non-blocking, batched tracking clients with sendBeacon, visibility events, and throttling so monitoring never slows your site down.",
    },
    {
        "title": "Introduction to Vector Databases",
        "category": "Data Science",
        "tags": ["vector-db", "pinecone", "similarity"],
        "price": 55.0,
        "level": "beginner",
        "description": "Similarity search, embeddings, index types and metadata filtering. A practical tour of Pinecone for semantic search and recommendation systems.",
    },
]


def seed_demo() -> None:
    db = SessionLocal()
    try:
        Base.metadata.create_all(bind=engine)
        existing = db.query(Product).count()
        for data in DEMO_PRODUCTS:
            title = data["title"]
            if db.query(Product).filter(Product.title == title).first():
                continue
            product_service.create_product(db, data)
        print(f"Seeded demo products (had {existing} before).")
        if not db.query(User).filter(User.role == "admin").first():
            create_admin("admin@smartreco.dev", "adminpass123", "Admin")
    finally:
        db.close()


def resync_vectors() -> None:
    db = SessionLocal()
    try:
        count = product_service.resync_all(db)
        print(f"Resynced {count} products to the vector store.")
    finally:
        db.close()


def _clean_price(value) -> float | None:
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    if price <= 0 or price > 20000:  # drop test listings / outliers
        return None
    return round(price, 2)


def _clean_stars(value) -> float | None:
    try:
        stars = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(5.0, stars))


def _clean_int(value) -> int | None:
    try:
        num = int(float(value))
    except (TypeError, ValueError):
        return None
    return max(0, num)


def _clean_bool(value) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def _derive_tags(category: str) -> list[str]:
    words = set()
    for token in category.replace("&", " ").replace(">", " ").split():
        token = token.strip().strip(",").lower()
        if token and len(token) > 2 and token not in {"and", "the", "for", "new"}:
            words.add(token)
    return sorted(words)


def clean_amazon_row(raw: dict) -> dict | None:
    title = (raw.get("title") or "").strip()
    category = (raw.get("categoryName") or "").strip()
    price = _clean_price(raw.get("price"))
    if not title or not category or price is None:
        return None
    img_url = (raw.get("imgUrl") or "").strip()
    return {
        "asin": (raw.get("asin") or "").strip() or None,
        "title": title[:255],
        "category": category[:100],
        "price": price,
        "image_url": img_url if img_url.startswith("http") else None,
        "product_url": (raw.get("productURL") or "").strip() or None,
        "stars": _clean_stars(raw.get("stars")),
        "reviews": _clean_int(raw.get("reviews")),
        "is_best_seller": _clean_bool(raw.get("isBestSeller")),
        "bought_in_last_month": _clean_int(raw.get("boughtInLastMonth")),
        "tags": _derive_tags(category),
        "description": "",
    }


def load_amazon(csv_path: str, limit: int | None = None, category: str | None = None) -> None:
    rows: list[dict] = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for raw in reader:
            cleaned = clean_amazon_row(raw)
            if cleaned is None:
                continue
            if category and category.lower() not in cleaned["category"].lower():
                continue
            rows.append(cleaned)
            if limit and len(rows) >= limit:
                break

    print(f"Valid rows to import: {len(rows)}")
    if not rows:
        return

    db = SessionLocal()
    try:
        new_ids = product_service.bulk_create_products(db, rows)
        print(f"Inserted {len(new_ids)} new products (skipped existing ASINs).")
        if new_ids:
            embedded = product_service.embed_product_batch(db, new_ids)
            print(f"Embedded {embedded} products into the vector store.")
    finally:
        db.close()


def check_vectors() -> None:
    from app.vector_store import get_vector_store

    db = SessionLocal()
    try:
        store = get_vector_store()
        print(f"Vector store count: {store.count()}")
        print(f"Active products in SQL: {db.query(Product).filter(Product.is_active.is_(True)).count()}")
    finally:
        db.close()


def run_digest_now() -> None:
    from app.services.digest import run_digest

    result = run_digest()
    print(result)


def set_telegram(email: str, chat_id: str) -> None:
    db = SessionLocal()
    try:
        user = auth_service.get_user_by_email(db, email)
        if user is None:
            print(f"User {email} not found.")
            return
        user.telegram_chat_id = chat_id.strip()
        db.commit()
        print(f"Set Telegram chat id {chat_id!r} for {email}.")
    finally:
        db.close()


def test_email(to: str, message: str | None = None) -> None:
    from app.services.digest import send_email

    message = message or "SmartReco test email. If you see this, email delivery is live."
    html = f"<p>{message}</p>"
    text = message
    ok = send_email(to, "SmartReco: email delivery test", html, text)
    backend = __import__("app.config", fromlist=["settings"]).settings.email_backend
    print("delivered" if ok else "FAILED", "| backend:", backend)


def telegram_chats() -> None:
    import httpx

    from app.config import settings

    if not settings.telegram_bot_token:
        print("TELEGRAM_BOT_TOKEN is not set in .env.")
        return
    resp = httpx.get(
        f"https://api.telegram.org/bot{settings.telegram_bot_token}/getUpdates", timeout=20
    )
    data = resp.json()
    chats: dict[str, str] = {}
    for update in data.get("result", []):
        msg = update.get("message") or update.get("channel_post") or {}
        chat = msg.get("chat") or {}
        if chat.get("id") is not None:
            label = chat.get("first_name") or chat.get("title") or chat.get("username") or "?"
            chats[str(chat["id"])] = label
    if not chats:
        print("No chats found. Open your bot in Telegram and send it a message first.")
        return
    for cid, label in chats.items():
        print(f"chat_id={cid}  name={label}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="app.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    admin = sub.add_parser("create_admin")
    admin.add_argument("--email", required=True)
    admin.add_argument("--password", required=True)
    admin.add_argument("--full-name", default="Admin")

    sub.add_parser("seed_demo")
    sub.add_parser("resync_vectors")
    sub.add_parser("check_vectors")
    sub.add_parser("digest")

    tg = sub.add_parser("set_telegram")
    tg.add_argument("--email", required=True)
    tg.add_argument("--chat-id", required=True)

    sub.add_parser("telegram_chats")

    mail = sub.add_parser("test_email")
    mail.add_argument("--to", required=True)
    mail.add_argument("--message", default=None)

    amazon = sub.add_parser("load_amazon")
    amazon.add_argument("--csv", required=True, help="Path to the Amazon UK 2023 CSV")
    amazon.add_argument("--limit", type=int, default=5000, help="Max products to import")
    amazon.add_argument("--category", default=None, help="Only import rows containing this category")

    args = parser.parse_args()

    if args.command == "create_admin":
        create_admin(args.email, args.password, args.full_name)
    elif args.command == "seed_demo":
        seed_demo()
    elif args.command == "load_amazon":
        load_amazon(args.csv, limit=args.limit, category=args.category)
    elif args.command == "resync_vectors":
        resync_vectors()
    elif args.command == "check_vectors":
        check_vectors()
    elif args.command == "digest":
        run_digest_now()
    elif args.command == "set_telegram":
        set_telegram(args.email, args.chat_id)
    elif args.command == "telegram_chats":
        telegram_chats()
    elif args.command == "test_email":
        test_email(args.to, args.message)


if __name__ == "__main__":
    sys.exit(main())
