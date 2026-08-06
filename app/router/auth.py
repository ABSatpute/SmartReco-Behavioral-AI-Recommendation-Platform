from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.deps import current_session
from app.flash import set_flash
from app.models import User
from app.services import auth as auth_service
from app.services import cart as cart_service
from app.templating import templates

router = APIRouter(prefix="/auth")


def set_session_cookie(response: RedirectResponse, session) -> None:
    response.set_cookie(
        key=settings.session_cookie,
        value=session.session_key,
        max_age=settings.session_ttl_days * 24 * 60 * 60,
        httponly=True,
        samesite="lax",
    )


@router.get("/register", response_class=HTMLResponse)
def register_form(request: Request, user: User | None = Depends(current_session)):
    if user and user.user_id:
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse(request, "auth/register.html", {"error": None})


@router.post("/register")
def register(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    full_name: str = Form(""),
    mobile: str = Form(...),
    age: int = Form(...),
    gender: str = Form(...),
    telegram_chat_id: str = Form(""),
    db: Session = Depends(get_db),
):
    email = email.strip().lower()
    if len(password) < 8:
        return templates.TemplateResponse(
            request, "auth/register.html", {"error": "Password must be at least 8 characters."},
            status_code=400,
        )
    if auth_service.get_user_by_email(db, email):
        return templates.TemplateResponse(
            request, "auth/register.html", {"error": "An account with this email already exists."},
            status_code=400,
        )
    if not mobile.strip():
        return templates.TemplateResponse(
            request, "auth/register.html", {"error": "Mobile number is required."},
            status_code=400,
        )
    if not (0 < age < 120):
        return templates.TemplateResponse(
            request, "auth/register.html", {"error": "Please enter a valid age."},
            status_code=400,
        )
    user = User(
        email=email,
        password_hash=auth_service.hash_password(password),
        full_name=full_name.strip(),
        role="user",
        mobile=mobile.strip(),
        age=age,
        gender=gender.strip() or None,
        telegram_chat_id=telegram_chat_id.strip() or None,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    session = auth_service.create_session(db, user)
    response = RedirectResponse(url="/", status_code=303)
    set_session_cookie(response, session)

    guest_session = auth_service.get_session(
        db, request.cookies.get(settings.session_cookie)
    )
    if guest_session is not None and guest_session.user_id is None:
        cart_service.merge_guest_into_user(db, user, guest_session.session_key)
    set_flash(response, f"Account created. Welcome to SmartReco, {user.full_name or user.email}!")
    return response


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request, session=Depends(current_session)):
    if session and session.user_id:
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse(request, "auth/login.html", {"error": None})


@router.post("/login")
def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = auth_service.authenticate_user(db, email, password)
    if user is None:
        return templates.TemplateResponse(
            request, "auth/login.html", {"error": "Invalid email or password."},
            status_code=400,
        )
    session = auth_service.create_session(db, user)
    response = RedirectResponse(url="/", status_code=303)
    set_session_cookie(response, session)

    guest_session = auth_service.get_session(
        db, request.cookies.get(settings.session_cookie)
    )
    if guest_session is not None and guest_session.user_id is None:
        cart_service.merge_guest_into_user(db, user, guest_session.session_key)
    set_flash(response, f"Welcome back, {user.full_name or user.email}!", "success")
    return response


@router.post("/logout")
def logout(request: Request, session=Depends(current_session), db: Session = Depends(get_db)):
    if session is not None:
        db.delete(session)
        db.commit()
    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie(settings.session_cookie)
    set_flash(response, "You've been logged out.", "info")
    return response
