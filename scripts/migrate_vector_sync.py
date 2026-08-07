"""Migration to add products.vector_synced_at (admin vector-sync status).

Idempotent; safe to run repeatedly against either Postgres (live) or SQLite
(local).

Usage:  python scripts/migrate_vector_sync.py
"""
import sys

from sqlalchemy import text

from app.database import engine


def _dialect() -> str:
    return engine.dialect.name


def _column_exists(table: str, column: str) -> bool:
    if _dialect() == "postgresql":
        rows = engine.connect().execute(
            text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = :t AND column_name = :c"
            ),
            {"t": table, "c": column},
        ).fetchall()
        return bool(rows)
    rows = engine.connect().execute(text(f"PRAGMA table_info({table})")).fetchall()
    return any(r[1] == column for r in rows)


def main() -> None:
    if _column_exists("products", "vector_synced_at"):
        print("products.vector_synced_at already exists — nothing to do.")
        return
    with engine.begin() as conn:
        conn.execute(
            text(
                "ALTER TABLE products ADD COLUMN vector_synced_at TIMESTAMP"
                if _dialect() == "postgresql"
                else "ALTER TABLE products ADD COLUMN vector_synced_at DATETIME"
            )
        )
    print("Migration complete: products.vector_synced_at added.")


if __name__ == "__main__":
    sys.exit(main())
