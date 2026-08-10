from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.deps import require_admin, require_user
from app.models import Product, Session as DBSession
from app.models import User, UserEvent
from app.schemas import CartAddIn, CartRemoveIn, CartUpdateIn, EventBatchIn, RecommendationOut
from app.services import auth as auth_service
from app.services import browse_sessions as browse_service
from app.services import cart as cart_service
from app.services import digest as digest_service
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


def cart_owner(request: Request, response: Response, db: Session):
    """Resolve who owns the cart. Logged-in users own a user cart; everyone
    else owns a guest cart keyed by their anonymous session."""
    session = auth_service.get_session(db, request.cookies.get(settings.session_cookie))
    if session is None:
        session = auth_service.create_session(db, None)
        request.state.new_session = session
    if session.user_id is not None:
        return session.user, None
    return None, session.session_key


def _set_new_session_cookie(response: Response, session: DBSession) -> None:
    response.set_cookie(
        key=settings.session_cookie,
        value=session.session_key,
        max_age=settings.session_ttl_days * 24 * 60 * 60,
        httponly=True,
        samesite="lax",
    )


@router.post("/cart/add")
def cart_add(
    payload: CartAddIn,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    user, session_key = cart_owner(request, response, db)
    if db.get(Product, payload.product_id) is None:
        return {"ok": False, "error": "Product not found."}
    count = cart_service.add_item(db, user, session_key, payload.product_id, payload.quantity)
    if getattr(request.state, "new_session", None) is not None:
        _set_new_session_cookie(response, request.state.new_session)
    return {"ok": True, "count": count}


@router.post("/cart/update")
def cart_update(
    payload: CartUpdateIn,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    user, session_key = cart_owner(request, response, db)
    count = cart_service.set_quantity(db, user, session_key, payload.product_id, payload.quantity)
    if getattr(request.state, "new_session", None) is not None:
        _set_new_session_cookie(response, request.state.new_session)
    return {"ok": True, "count": count}


@router.post("/cart/remove")
def cart_remove(
    payload: CartRemoveIn,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    user, session_key = cart_owner(request, response, db)
    count = cart_service.remove_item(db, user, session_key, payload.product_id)
    if getattr(request.state, "new_session", None) is not None:
        _set_new_session_cookie(response, request.state.new_session)
    return {"ok": True, "count": count}


@router.get("/cart")
def cart_summary(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    user, session_key = cart_owner(request, response, db)
    cart = cart_service.get_cart(db, user, session_key)
    if getattr(request.state, "new_session", None) is not None:
        _set_new_session_cookie(response, request.state.new_session)
    return {"ok": True, "count": cart["count"], "subtotal": cart["subtotal"]}


@router.post("/cart/checkout")
def cart_checkout(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    user, session_key = cart_owner(request, response, db)
    cart = cart_service.checkout(db, user, session_key)
    if getattr(request.state, "new_session", None) is not None:
        _set_new_session_cookie(response, request.state.new_session)
    return {"ok": True, "count": cart["count"], "subtotal": cart["subtotal"]}



@router.post("/events/batch")
def ingest_events(
    batch: EventBatchIn,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    session = ensure_session(request, db)
    new_session = getattr(request.state, "new_session", None)
    browse = browse_service.touch_or_create(db, session.user_id, session.session_key)

    stored = 0
    for event in batch.events:
        if event.event_type not in VALID_EVENT_TYPES:
            continue
        db.add(
            UserEvent(
                user_id=session.user_id,
                session_id=session.id,
                browse_session_id=browse.id,
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

    # Behaviour-triggered recommendations: when enough meaningful activity has
    # accumulated (trigger policy + cooldown), generate fresh picks in the
    # background so a notification can nudge the user. Skipped in tests.
    if session.user_id is not None and settings.app_env != "test":
        rec_service.schedule_background(session.user_id)

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


@router.get("/recommendations/status")
def api_recommendation_status(
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """Lightweight poll for the browser notification: is a fresh recommendation
    waiting? Returns the newest valid recommendation id so the client can tell
    when a *new* one was generated while the user browsed."""
    recommendation = rec_service.valid_latest(db, user.id)
    if recommendation is None:
        return {"ready": False, "rec_id": None, "summary": None}
    return {
        "ready": True,
        "rec_id": recommendation.id,
        "summary": recommendation.summary,
        "created_at": recommendation.created_at.isoformat(sep=" ", timespec="seconds"),
    }


@router.post("/digest/test")
def api_digest_test(
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Manual digest trigger for admins (dev/debug)."""
    return digest_service.run_digest(db)
