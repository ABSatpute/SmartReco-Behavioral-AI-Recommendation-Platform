"""One-time migration to add browsing-session tables + column.

Idempotent; safe to run repeatedly against either Postgres (live) or SQLite
(local). Uses raw SQL so it works before the models' FK constraint exists.

Usage:  python scripts/migrate_sessions.py
"""
import sys

from sqlalchemy import text

from app.config import settings
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
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS browse_sessions (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                    session_key VARCHAR(64),
                    started_at TIMESTAMP NOT NULL,
                    last_seen_at TIMESTAMP NOT NULL,
                    ended_at TIMESTAMP
                )
                """
                if _dialect() == "postgresql"
                else """
                CREATE TABLE IF NOT EXISTS browse_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                    session_key VARCHAR(64),
                    started_at DATETIME NOT NULL,
                    last_seen_at DATETIME NOT NULL,
                    ended_at DATETIME
                )
                """
            )
        )
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_browse_sessions_user_id ON browse_sessions(user_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_browse_sessions_session_key ON browse_sessions(session_key)"))

        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS session_digests (
                    id SERIAL PRIMARY KEY,
                    browse_session_id INTEGER NOT NULL REFERENCES browse_sessions(id) ON DELETE CASCADE,
                    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                    slot VARCHAR(8) NOT NULL,
                    recommendation_id INTEGER REFERENCES recommendations(id) ON DELETE SET NULL,
                    status VARCHAR(16) NOT NULL DEFAULT 'sent',
                    sent_at TIMESTAMP,
                    created_at TIMESTAMP NOT NULL,
                    CONSTRAINT uq_session_digest_slot UNIQUE (browse_session_id, slot)
                )
                """
                if _dialect() == "postgresql"
                else """
                CREATE TABLE IF NOT EXISTS session_digests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    browse_session_id INTEGER NOT NULL REFERENCES browse_sessions(id) ON DELETE CASCADE,
                    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                    slot VARCHAR(8) NOT NULL,
                    recommendation_id INTEGER REFERENCES recommendations(id) ON DELETE SET NULL,
                    status VARCHAR(16) NOT NULL DEFAULT 'sent',
                    sent_at DATETIME,
                    created_at DATETIME NOT NULL,
                    UNIQUE (browse_session_id, slot)
                )
                """
            )
        )
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_session_digests_browse_session_id ON session_digests(browse_session_id)"))

        if not _column_exists("user_events", "browse_session_id"):
            conn.execute(
                text(
                    "ALTER TABLE user_events ADD COLUMN browse_session_id INTEGER REFERENCES browse_sessions(id) ON DELETE SET NULL"
                )
            )
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_user_events_browse_session_id ON user_events(browse_session_id)"))

    print("Migration complete: browse_sessions, session_digests, user_events.browse_session_id")


if __name__ == "__main__":
    sys.exit(main())
