from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.deps import require_user
from app.models import Session as DBSession
from app.models import User, UserEvent
from app.schemas import EventBatchIn, RecommendationOut
from app.services import auth as auth_service
from app.services import recommendations as rec_service

router = APIRouter(prefix="/api")

VALID_EVENT_TYPES = {
    "page_view",
    "product_view",
    "product_click",
    "category_click",
    "search",
    "add_to_cart",
    "purchase",
    "time_spent",
}


def ensure_session(request: Request, db: Session) -> DBSession:
    session = auth_service.get_session(db, request.cookies.get(settings.session_cookie))
    if session is None:
        session = auth_service.create_session(db, None)
        request.state.new_session = session
    return session


@router.post("/events/batch")
def ingest_events(
    batch: EventBatchIn,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    session = ensure_session(request, db)
    new_session = getattr(request.state, "new_session", None)

    stored = 0
    for event in batch.events:
        if event.event_type not in VALID_EVENT_TYPES:
            continue
        db.add(
            UserEvent(
                user_id=session.user_id,
                session_id=session.id,
                event_type=event.event_type,
                entity_type=event.entity_type,
                entity_id=event.entity_id,
                payload=event.payload,
            )
        )
        stored += 1

    db.commit()

    if new_session is not None:
        response.set_cookie(
            key=settings.session_cookie,
            value=new_session.session_key,
            max_age=settings.session_ttl_days * 24 * 60 * 60,
            httponly=True,
            samesite="lax",
        )

    return {"status": "ok", "stored": stored}


@router.get("/recommendations/latest")
def api_latest_recommendation(
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    recommendation = rec_service.valid_latest(db, user.id)
    if recommendation is None:
        return {"status": "none", "message": "No valid recommendation yet. Browse a little more."}
    return RecommendationOut.model_validate(recommendation)
