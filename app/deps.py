from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.services import auth as auth_service


def current_session(
    request: Request, db: Session = Depends(get_db)
) -> auth_service.DBSession | None:
    return auth_service.get_session(db, request.cookies.get("smartreco_session"))


def current_user(
    session: auth_service.DBSession | None = Depends(current_session),
) -> User | None:
    if session is None or session.user_id is None:
        return None
    return session.user


def require_user(
    user: User | None = Depends(current_user),
) -> User:
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Please log in."
        )
    return user


def require_admin(user: User = Depends(require_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin only.")
    return user
