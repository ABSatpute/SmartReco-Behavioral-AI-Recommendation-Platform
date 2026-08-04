import secrets
from datetime import timedelta

import bcrypt
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Session as DBSession
from app.models import User
from app.utils import utcnow_naive


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def create_session(db: Session, user: User | None = None) -> DBSession:
    session = DBSession(
        user_id=user.id if user else None,
        session_key=secrets.token_urlsafe(48)[:64],
        created_at=utcnow_naive(),
        expires_at=utcnow_naive() + timedelta(days=settings.session_ttl_days),
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def get_session(db: Session, session_key: str | None) -> DBSession | None:
    if not session_key:
        return None
    session = (
        db.query(DBSession)
        .filter(DBSession.session_key == session_key)
        .first()
    )
    if session is None:
        return None
    if session.expires_at and session.expires_at < utcnow_naive():
        return None
    return session


def authenticate_user(db: Session, email: str, password: str) -> User | None:
    user = db.query(User).filter(User.email == email.lower().strip()).first()
    if user is None or not verify_password(password, user.password_hash):
        return None
    return user


def get_user_by_email(db: Session, email: str) -> User | None:
    return db.query(User).filter(User.email == email.lower().strip()).first()
