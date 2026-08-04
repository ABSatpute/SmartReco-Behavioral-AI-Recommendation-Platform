"""CLI helpers: python -m app.cli <command>

Commands:
  create_admin --email E --password P         create an admin user
  seed_demo                                   seed demo products + demo admin
  resync_vectors                              re-embed all active products to Pinecone
  check_vectors                               report vector store health
"""
import argparse
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


def check_vectors() -> None:
    from app.vector_store import get_vector_store

    db = SessionLocal()
    try:
        store = get_vector_store()
        print(f"Vector store count: {store.count()}")
        print(f"Active products in SQL: {db.query(Product).filter(Product.is_active.is_(True)).count()}")
    finally:
        db.close()


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

    args = parser.parse_args()

    if args.command == "create_admin":
        create_admin(args.email, args.password, args.full_name)
    elif args.command == "seed_demo":
        seed_demo()
    elif args.command == "resync_vectors":
        resync_vectors()
    elif args.command == "check_vectors":
        check_vectors()


if __name__ == "__main__":
    sys.exit(main())
