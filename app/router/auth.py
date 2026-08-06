import re

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.deps import current_session
from app.flash import set_flash
from app.models import User
from app.rate_limit import allow as rate_limit_allow
from app.rate_limit import reset as rate_limit_reset
from app.services import auth as auth_service
from app.services import cart as cart_service
from app.templating import templates

router = APIRouter(prefix="/auth")


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def set_session_cookie(response: RedirectResponse, session) -> None:
    response.set_cookie(
        key=settings.session_cookie,
        value=session.session_key,
        max_age=settings.session_ttl_days * 24 * 60 * 60,
        httponly=True,
        samesite="lax",
        secure=settings.app_env == "production",
    )


@router.get("/register", response_class=HTMLResponse)
def register_form(request: Request, user: User | None = Depends(current_session)):
    if user and user.user_id:
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse(request, "auth/register.html", {"error": None, "errors": None, "form": None})


def _valid_email(value: str) -> bool:
    return bool(re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value))


@router.post("/register")
def register(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    full_name: str = Form(""),
    mobile: str = Form(...),
    age: str = Form(...),
    gender: str = Form(""),
    telegram_chat_id: str = Form(""),
    db: Session = Depends(get_db),
):
    if not rate_limit_allow(
        f"register:{client_ip(request)}",
        limit=settings.auth_rate_limit,
        window_seconds=settings.auth_rate_window_seconds,
    ):
        return templates.TemplateResponse(
            request, "auth/register.html",
            {"error": "Too many attempts from this address. Please try again in 15 minutes.",
             "errors": None, "form": None},
            status_code=429,
        )

    email = email.strip().lower()
    full_name = full_name.strip()
    mobile = mobile.strip()
    gender = gender.strip()
    telegram_chat_id = telegram_chat_id.strip()
    form = {
        "email": email,
        "full_name": full_name,
        "mobile": mobile,
        "age": age.strip(),
        "gender": gender,
        "telegram_chat_id": telegram_chat_id,
    }

    errors: dict[str, str] = {}
    if len(full_name) < 2:
        errors["full_name"] = "Please enter your full name."
    if not _valid_email(email):
        errors["email"] = "Please enter a valid email address."
    elif auth_service.get_user_by_email(db, email):
        errors["email"] = "An account with this email already exists."
    digits_only = re.sub(r"[^\d]", "", mobile)
    if not (7 <= len(digits_only) <= 15):
        errors["mobile"] = "Enter a valid mobile number (7–15 digits)."
    try:
        age_val = int(age)
    except (TypeError, ValueError):
        errors["age"] = "Please enter a valid age."
    else:
        if not (1 <= age_val <= 119):
            errors["age"] = "Please enter an age between 1 and 119."
    if not gender:
        errors["gender"] = "Please select a gender."
    if len(password) < 8:
        errors["password"] = "Password must be at least 8 characters."

    if errors:
        return templates.TemplateResponse(
            request, "auth/register.html",
            {"error": next(iter(errors.values())), "errors": errors, "form": form},
            status_code=400,
        )

    user = User(
        email=email,
        password_hash=auth_service.hash_password(password),
        full_name=full_name,
        role="user",
        mobile=mobile,
        age=age_val,
        gender=gender or None,
        telegram_chat_id=telegram_chat_id or None,
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
    rate_limit_reset(f"register:{client_ip(request)}")
    return response


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request, session=Depends(current_session)):
    if session and session.user_id:
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse(request, "auth/login.html", {"error": None, "errors": None, "form": None})


@router.post("/login")
def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    if not rate_limit_allow(
        client_ip(request),
        limit=settings.auth_rate_limit,
        window_seconds=settings.auth_rate_window_seconds,
    ):
        return templates.TemplateResponse(
            request, "auth/login.html",
            {"error": "Too many attempts from this address. Please try again in 15 minutes.",
             "errors": None, "form": {"email": email.strip()}},
            status_code=429,
        )
    user = auth_service.authenticate_user(db, email.strip(), password)
    if user is None:
        return templates.TemplateResponse(
            request, "auth/login.html",
            {"error": "Invalid email or password.",
             "errors": None, "form": {"email": email.strip()}},
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
    rate_limit_reset(client_ip(request))
    return response


@router.post("/logout")
def logout(request: Request, session=Depends(current_session), db: Session = Depends(get_db)):
    if session is not None:
        db.delete(session)
        db.commit()
    response = RedirectResponse(url="/auth/login", status_code=303)
    response.delete_cookie(settings.session_cookie)
    set_flash(response, "You've been logged out.", "info")
    return response
