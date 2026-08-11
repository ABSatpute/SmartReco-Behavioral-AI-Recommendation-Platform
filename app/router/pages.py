import re
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import current_session, current_user, require_user
from app.flash import set_flash
from app.models import Product, User
from app.services import auth as auth_service
from app.services import cart as cart_service
from app.services import products as product_service
from app.services import recommendations as rec_service
from app.templating import templates

router = APIRouter()


def _nav_context(request: Request, user: User | None, db: Session) -> dict:
    """Amazon-style sub-nav categories + personalized recommendation for the homepage."""
    nav_categories = product_service.top_categories(db, limit=10)
    recommendation = None
    reco_picks: list = []
    if user is not None:
        recommendation = rec_service.valid_latest(db, user.id)
        reco_picks = rec_service.with_products(db, recommendation)
    return {
        "nav_categories": nav_categories,
        "recommendation": recommendation,
        "reco_picks": reco_picks,
        "now": datetime.now(UTC),
    }


def _render_home(request: Request, user: User, db: Session):
    products = (
        db.query(Product)
        .filter(Product.is_active.is_(True))
        .order_by(Product.created_at.desc())
        .limit(48)
        .all()
    )
    ctx = _nav_context(request, user, db)
    if user.role == "admin":
        ctx["recommendation"] = None
        ctx["reco_picks"] = []
    ctx.update(
        {
            "current_user": user,
            "products": products,
            "hide_hero": user.role == "admin",
            "title": "Shop · SmartReco",
        }
    )
    return templates.TemplateResponse(request, "index.html", ctx)


@router.get("/", response_class=HTMLResponse)
def home(
    request: Request,
    user: User | None = Depends(current_user),
    db: Session = Depends(get_db),
):
    if user is None:
        return RedirectResponse(url="/auth/login", status_code=302)
    if user.role == "admin":
        return RedirectResponse(url="/admin", status_code=302)
    return _render_home(request, user, db)


@router.get("/store", response_class=HTMLResponse)
def store_preview(
    request: Request,
    user: User | None = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Storefront preview for admins: renders the user-facing home page
    without redirecting to the admin dashboard."""
    if user is None:
        return RedirectResponse(url="/auth/login", status_code=302)
    return _render_home(request, user, db)


def _valid_email(value: str) -> bool:
    return bool(re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value))


def _account_form(user: User, submitted: dict | None = None) -> dict:
    form = {
        "full_name": user.full_name or "",
        "email": user.email or "",
        "mobile": user.mobile or "",
        "age": str(user.age) if user.age is not None else "",
        "gender": user.gender or "",
        "telegram_chat_id": user.telegram_chat_id or "",
    }
    if submitted:
        form.update({k: v for k, v in submitted.items() if k in form})
    return form


@router.get("/account", response_class=HTMLResponse)
def account_page(
    request: Request,
    user: User | None = Depends(current_user),
    db: Session = Depends(get_db),
):
    if user is None:
        return RedirectResponse(url="/auth/login", status_code=302)
    return templates.TemplateResponse(
        request,
        "account.html",
        {
            "current_user": user,
            "form": _account_form(user),
            "errors": None,
            "now": datetime.now(UTC),
            "title": "Account · SmartReco",
        },
    )


@router.post("/account")
def account_update(
    request: Request,
    email: str = Form(...),
    full_name: str = Form(""),
    mobile: str = Form(...),
    age: str = Form(...),
    gender: str = Form(""),
    telegram_chat_id: str = Form(""),
    current_password: str = Form(""),
    new_password: str = Form(""),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    email = email.strip().lower()
    full_name = full_name.strip()
    mobile = mobile.strip()
    gender = gender.strip()
    telegram_chat_id = telegram_chat_id.strip()
    submitted = {
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
    else:
        existing = auth_service.get_user_by_email(db, email)
        if existing is not None and existing.id != user.id:
            errors["email"] = "An account with this email already exists."
    digits_only = re.sub(r"[^\d]", "", mobile)
    if not (7 <= len(digits_only) <= 15):
        errors["mobile"] = "Enter a valid mobile number (7-15 digits)."
    try:
        age_val = int(age)
    except (TypeError, ValueError):
        errors["age"] = "Please enter a valid age."
    else:
        if not (1 <= age_val <= 119):
            errors["age"] = "Please enter an age between 1 and 119."
    if not gender:
        errors["gender"] = "Please select a gender."

    if current_password or new_password:
        if not current_password or not auth_service.verify_password(
            current_password, user.password_hash
        ):
            errors["current_password"] = "Current password is incorrect."
        elif len(new_password) < 8:
            errors["new_password"] = "New password must be at least 8 characters."
        elif new_password == current_password:
            errors["new_password"] = "New password must be different from the current one."

    if errors:
        return templates.TemplateResponse(
            request,
            "account.html",
            {
                "current_user": user,
                "form": _account_form(user, submitted),
                "errors": errors,
                "now": datetime.now(UTC),
                "title": "Account · SmartReco",
            },
            status_code=400,
        )

    user.email = email
    user.full_name = full_name
    user.mobile = mobile
    user.age = age_val
    user.gender = gender or None
    user.telegram_chat_id = telegram_chat_id or None
    if current_password:
        user.password_hash = auth_service.hash_password(new_password)
    db.commit()

    response = RedirectResponse(url="/account", status_code=303)
    set_flash(response, "Account details updated.", "success")
    return response


@router.get("/products/{slug}", response_class=HTMLResponse)
def product_detail(
    slug: str,
    request: Request,
    user: User | None = Depends(current_user),
    db: Session = Depends(get_db),
):
    product = product_service.get_product_by_slug(db, slug)
    if product is None:
        return templates.TemplateResponse(
            request,
            "product_detail.html",
            {"current_user": user, "product": None},
            status_code=404,
        )
    ctx = _nav_context(request, user, db)
    ctx.update({"current_user": user, "product": product})
    return templates.TemplateResponse(request, "product_detail.html", ctx)


@router.get("/search", response_class=HTMLResponse)
def search_page(
    request: Request,
    q: str = "",
    category: str = "",
    user: User | None = Depends(current_user),
    db: Session = Depends(get_db),
):
    q = q.strip()
    cat = category.strip()
    if q:
        products = product_service.search_products(db, q, category=cat or None)
    elif cat:
        products = product_service.products_by_category(db, cat)
    else:
        products = []
    ctx = _nav_context(request, user, db)
    ctx.update(
        {
            "current_user": user,
            "query": q,
            "category": category,
            "products": products,
        }
    )
    return templates.TemplateResponse(request, "search.html", ctx)


@router.get("/recommendations", response_class=HTMLResponse)
def recommendations_page(
    request: Request,
    user: User | None = Depends(current_user),
    db: Session = Depends(get_db),
):
    if user is None:
        return RedirectResponse(url="/auth/login", status_code=303)
    recommendation = rec_service.ensure(db, user)
    last_run = rec_service.last_run(db, user.id)
    ctx = _nav_context(request, user, db)
    ctx.update(
        {
            "current_user": user,
            "recommendation": recommendation,
            "picks": rec_service.with_products(db, recommendation),
            "last_run": last_run,
        }
    )
    return templates.TemplateResponse(request, "recommendations.html", ctx)


@router.get("/cart", response_class=HTMLResponse)
def cart_page(
    request: Request,
    session=Depends(current_session),
    user: User | None = Depends(current_user),
    db: Session = Depends(get_db),
):
    session_key = session.session_key if session is not None else None
    cart = cart_service.get_cart(db, user, session_key)
    ctx = _nav_context(request, user, db)
    ctx.update({"current_user": user, "cart": cart, "title": "Cart · SmartReco"})
    return templates.TemplateResponse(request, "cart.html", ctx)


@router.post("/recommendations/refresh")
def refresh_recommendations(
    request: Request,
    user: User | None = Depends(current_user),
    db: Session = Depends(get_db),
):
    if user is None:
        return RedirectResponse(url="/auth/login", status_code=303)
    rec_service.refresh(db, user)
    response = RedirectResponse(url="/recommendations", status_code=303)
    set_flash(response, "Your recommendations have been refreshed.")
    return response
