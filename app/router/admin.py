import json as jsonlib
from collections.abc import AsyncIterator, Awaitable, Callable

import anyio
import time
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from sqlalchemy import String, func
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import require_admin
from app.flash import set_flash
from app.models import AgentRun, Product, Recommendation, User, UserEvent
from app.observability import current_trace_id, langsmith_enabled
from app.services import auth as auth_service
from app.services import products as product_service
from app.templating import templates
from app.vector_store import get_vector_store

router = APIRouter(prefix="/admin")


def _vector_count() -> int | None:
    try:
        return get_vector_store().count()
    except Exception:  # noqa: BLE001 - vector store may be down/misconfigured
        return None


@router.get("", response_class=HTMLResponse)
def admin_dashboard(
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    total_products = db.query(func.count(Product.id)).scalar() or 0
    active_products = (
        db.query(func.count(Product.id)).filter(Product.is_active.is_(True)).scalar() or 0
    )
    total_users = db.query(func.count(User.id)).scalar() or 0
    event_count = db.query(func.count(UserEvent.id)).scalar() or 0
    reco_count = db.query(func.count(Recommendation.id)).scalar() or 0
    total_runs = db.query(func.count(AgentRun.id)).scalar() or 0
    failed_runs = (
        db.query(func.count(AgentRun.id)).filter(AgentRun.error.isnot(None)).scalar() or 0
    )

    recent_runs = (
        db.query(AgentRun).order_by(AgentRun.created_at.desc()).limit(6).all()
    )
    recent_products = (
        db.query(Product).order_by(Product.created_at.desc()).limit(6).all()
    )

    return templates.TemplateResponse(
        request,
        "admin/dashboard.html",
        {
            "current_user": user,
            "total_products": total_products,
            "active_products": active_products,
            "inactive_products": total_products - active_products,
            "total_users": total_users,
            "event_count": event_count,
            "reco_count": reco_count,
            "total_runs": total_runs,
            "failed_runs": failed_runs,
            "successful_runs": total_runs - failed_runs,
            "vector_count": _vector_count(),
            "recent_runs": recent_runs,
            "recent_products": recent_products,
        },
    )


def _parse_tags(raw: str) -> list[str]:
    return [t.strip() for t in raw.replace(",", " ").split() if t.strip()]


def _display_form(product: Product | None = None, raw: dict | None = None) -> dict:
    """Display values for the form inputs (strings), from a product or submitted raw form."""
    if raw is not None:
        return {
            "title": raw.get("title", ""),
            "description": raw.get("description", ""),
            "category": raw.get("category", ""),
            "price": raw.get("price", ""),
            "level": raw.get("level", ""),
            "image_url": raw.get("image_url", ""),
            "tags": raw.get("tags", ""),
            "product_url": raw.get("product_url", ""),
            "asin": raw.get("asin", ""),
            "stars": raw.get("stars", ""),
            "reviews": raw.get("reviews", ""),
            "is_best_seller": raw.get("is_best_seller", False),
            "bought_in_last_month": raw.get("bought_in_last_month", ""),
        }
    if product is None:
        return {
            "title": "", "description": "", "category": "", "price": "", "level": "",
            "image_url": "", "tags": "", "product_url": "", "asin": "", "stars": "",
            "reviews": "", "is_best_seller": False, "bought_in_last_month": "",
        }
    return {
        "title": product.title or "",
        "description": product.description or "",
        "category": product.category or "",
        "price": str(product.price) if product.price is not None else "",
        "level": product.level or "",
        "image_url": product.image_url or "",
        "tags": " ".join(product.tags or []),
        "product_url": product.product_url or "",
        "asin": product.asin or "",
        "stars": str(product.stars) if product.stars is not None else "",
        "reviews": str(product.reviews) if product.reviews is not None else "",
        "is_best_seller": bool(product.is_best_seller),
        "bought_in_last_month": str(product.bought_in_last_month)
        if product.bought_in_last_month is not None
        else "",
    }


def _form_data(
    title: str,
    description: str,
    category: str,
    price: str,
    level: str,
    image_url: str,
    tags: str,
    product_url: str,
    asin: str,
    stars: str,
    reviews: str,
    is_best_seller: bool,
    bought_in_last_month: str,
) -> tuple[dict, dict]:
    """Validate submitted product fields. Returns (parsed_data, errors)."""
    errors: dict[str, str] = {}
    data: dict = {
        "title": title.strip(),
        "description": description,
        "category": category.strip(),
        "level": level.strip() or None,
        "image_url": image_url.strip() or None,
        "tags": _parse_tags(tags),
        "product_url": product_url.strip() or None,
        "asin": asin.strip() or None,
        "is_best_seller": bool(is_best_seller),
    }
    if not data["title"]:
        errors["title"] = "Title is required."
    if not data["category"]:
        errors["category"] = "Category is required."
    try:
        price_val = float(price)
        if price_val < 0:
            raise ValueError
        data["price"] = round(price_val, 2)
    except (TypeError, ValueError):
        errors["price"] = "Enter a valid price (0 or more)."

    def _opt(key: str, integer: bool):
        raw = {
            "stars": stars,
            "reviews": reviews,
            "bought_in_last_month": bought_in_last_month,
        }[key].strip()
        if not raw:
            return None
        try:
            return int(raw) if integer else float(raw)
        except ValueError:
            errors[key] = "Enter a valid number."
            return None

    stars_val = _opt("stars", integer=False)
    if stars_val is not None and not (0 <= stars_val <= 5):
        errors["stars"] = "Stars must be between 0 and 5."
    data["stars"] = stars_val

    reviews_val = _opt("reviews", integer=True)
    if reviews_val is not None and reviews_val < 0:
        errors["reviews"] = "Reviews can't be negative."
    data["reviews"] = reviews_val

    bought_val = _opt("bought_in_last_month", integer=True)
    if bought_val is not None and bought_val < 0:
        errors["bought_in_last_month"] = "This can't be negative."
    data["bought_in_last_month"] = bought_val

    return data, errors


@router.get("/products", response_class=HTMLResponse)
def admin_products(
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
    q: str = "",
    category: str = "",
    status: str = "",
    page: int = 1,
):
    per_page = 20
    query = db.query(Product)
    if q.strip():
        like = f"%{q.strip()}%"
        query = query.filter(
            (Product.title.ilike(like))
            | (Product.description.ilike(like))
            | (Product.category.ilike(like))
            | (Product.tags.cast(String).ilike(like))
        )
    if category.strip():
        query = query.filter(Product.category.ilike(f"%{category.strip()}%"))
    if status == "active":
        query = query.filter(Product.is_active.is_(True))
    elif status == "inactive":
        query = query.filter(Product.is_active.is_(False))

    total = query.count()
    from math import ceil

    pages = max(1, ceil(total / per_page))
    page = max(1, min(page, pages))
    products = (
        query.order_by(Product.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

    return templates.TemplateResponse(
        request,
        "admin/product_list.html",
        {
            "current_user": user,
            "products": products,
            "total": total,
            "page": page,
            "pages": pages,
            "q": q,
            "category": category,
            "status": status,
            "categories": product_service.top_categories(db),
        },
    )


@router.get("/products/new", response_class=HTMLResponse)
def new_product_form(request: Request, user: User = Depends(require_admin)):
    return templates.TemplateResponse(
        request,
        "admin/product_form.html",
        {"current_user": user, "product": None, "error": None, "errors": None, "form": _display_form()},
    )


@router.post("/products", response_class=HTMLResponse)
def create_product(
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
    title: str = Form(""),
    description: str = Form(""),
    category: str = Form(""),
    price: str = Form(""),
    level: str = Form(""),
    image_url: str = Form(""),
    tags: str = Form(""),
    product_url: str = Form(""),
    asin: str = Form(""),
    stars: str = Form(""),
    reviews: str = Form(""),
    is_best_seller: str = Form(""),
    bought_in_last_month: str = Form(""),
):
    data, errors = _form_data(
        title, description, category, price, level, image_url, tags,
        product_url, asin, stars, reviews, is_best_seller in ("on", "true", "1"), bought_in_last_month,
    )
    if errors:
        return templates.TemplateResponse(
            request,
            "admin/product_form.html",
            {
                "current_user": user,
                "product": None,
                "error": next(iter(errors.values())),
                "errors": errors,
                "form": _display_form(raw=locals()),
            },
            status_code=400,
        )
    product = product_service.create_product(db, data)
    response = RedirectResponse(url=f"/admin/products/{product.id}/edit", status_code=303)
    set_flash(response, f"Product created: {product.title}")
    return response

@router.get("/products/{product_id}/edit", response_class=HTMLResponse)
def edit_product_form(
    product_id: int,
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found.")
    return templates.TemplateResponse(
        request,
        "admin/product_form.html",
        {
            "current_user": user,
            "product": product,
            "error": None,
            "errors": None,
            "form": _display_form(product=product),
        },
    )


@router.post("/products/resync-all")
def resync_all_products(
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    count = product_service.resync_all(db)
    response = RedirectResponse(url="/admin/products", status_code=303)
    set_flash(response, f"Synced vectors for {count} active product(s) missing embeddings.", "success")
    return response


@router.post("/products/{product_id}", response_class=HTMLResponse)
def update_product(
    product_id: int,
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
    title: str = Form(""),
    description: str = Form(""),
    category: str = Form(""),
    price: str = Form(""),
    level: str = Form(""),
    image_url: str = Form(""),
    tags: str = Form(""),
    product_url: str = Form(""),
    asin: str = Form(""),
    stars: str = Form(""),
    reviews: str = Form(""),
    is_best_seller: str = Form(""),
    bought_in_last_month: str = Form(""),
):
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found.")
    data, errors = _form_data(
        title, description, category, price, level, image_url, tags,
        product_url, asin, stars, reviews, is_best_seller in ("on", "true", "1"), bought_in_last_month,
    )
    if errors:
        return templates.TemplateResponse(
            request,
            "admin/product_form.html",
            {
                "current_user": user,
                "product": product,
                "error": next(iter(errors.values())),
                "errors": errors,
                "form": _display_form(raw=locals()),
            },
            status_code=400,
        )
    product_service.update_product(db, product, data)
    response = RedirectResponse(url="/admin/products", status_code=303)
    set_flash(response, f"Product updated: {product.title}")
    return response


@router.post("/products/{product_id}/delete")
def delete_product(
    product_id: int,
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found.")
    product_service.delete_product(db, product)
    response = RedirectResponse(url="/admin/products", status_code=303)
    set_flash(response, f"Product archived: {product.title}", "info")
    return response


@router.get("/products/{product_id}/resync")
def resync_product_vector(
    product_id: int,
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found.")
    product_service.sync_product_vector(db, product)
    db.refresh(product)
    response = RedirectResponse(url="/admin/products", status_code=303)
    if product.vector_synced_at:
        set_flash(response, f"Vector re-synced: {product.title}", "success")
    else:
        set_flash(response, f"Vector sync failed for {product.title} — check Pinecone config.", "error")
    return response


@router.get("/products/{product_id}/restore", response_class=HTMLResponse)
def restore_product(
    product_id: int,
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found.")
    product.is_active = True
    db.commit()
    product_service.sync_product_vector(db, product)
    response = RedirectResponse(url="/admin/products", status_code=303)
    set_flash(response, f"Product restored: {product.title}", "success")
    return response


@router.get("/observability", response_class=HTMLResponse)
def observability_page(
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    recent_runs = (
        db.query(AgentRun)
        .order_by(AgentRun.created_at.desc())
        .limit(50)
        .all()
    )
    run_counts = (
        db.query(AgentRun).filter(AgentRun.error.is_(None)).count(),
        db.query(AgentRun).filter(AgentRun.error.isnot(None)).count(),
    )
    event_count = db.query(UserEvent).count()
    return templates.TemplateResponse(
        request,
        "admin/observability.html",
        {
            "current_user": user,
            "recent_runs": recent_runs,
            "total_runs": sum(run_counts),
            "successful_runs": run_counts[0],
            "failed_runs": run_counts[1],
            "event_count": event_count,
            "langsmith_enabled": langsmith_enabled(),
            "current_trace_id": current_trace_id(),
        },
    )


# ---------------------------------------------------------------------------
# Live activity (SSE stream of behavioral events)
# ---------------------------------------------------------------------------

EVENT_LABELS = {
    "page_view": "Page view",
    "product_view": "Product view",
    "product_click": "Product click",
    "category_click": "Category click",
    "search": "Search",
    "add_to_cart": "Add to cart",
    "purchase": "Purchase",
    "time_spent": "Time spent",
}


def _event_card(event: UserEvent, email: str | None) -> dict:
    return {
        "id": event.id,
        "user_id": event.user_id,
        "email": email or f"guest:{event.session_id}",
        "event_type": event.event_type,
        "label": EVENT_LABELS.get(event.event_type, event.event_type),
        "entity_type": event.entity_type,
        "entity_id": event.entity_id,
        "payload": event.payload or {},
        "occurred_at": event.occurred_at.isoformat(sep=" ", timespec="seconds"),
    }


def _query_events(db: Session, after_id: int, limit: int = 50) -> list[dict]:
    rows = (
        db.query(UserEvent, User.email)
        .outerjoin(User, User.id == UserEvent.user_id)
        .filter(UserEvent.id > after_id)
        .order_by(UserEvent.id.asc())
        .limit(limit)
        .all()
    )
    return [_event_card(event, email) for event, email in rows]


@router.get("/live", response_class=HTMLResponse)
def live_activity_page(
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    recent = _query_events(db, after_id=0, limit=50)
    last_id = recent[-1]["id"] if recent else 0
    return templates.TemplateResponse(
        request,
        "admin/live.html",
        {
            "current_user": user,
            "recent_events": recent,
            "last_event_id": last_id,
            "event_types": sorted(EVENT_LABELS),
        },
    )


@router.get("/events/recent")
def live_activity_recent(
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
    limit: int = 50,
):
    rows = _query_events(db, after_id=0, limit=limit)
    return {"events": rows, "last_id": rows[-1]["id"] if rows else 0}


async def _event_stream(
    db: Session,
    after: int,
    is_disconnected: Callable[[], Awaitable[bool]],
) -> AsyncIterator[str]:
    """Yield SSE frames for user_events newer than ``after``.

    Polls the DB every second (indexed PK range scan) until the caller's
    ``is_disconnected`` callable returns True. Both the endpoint and tests
    drive this directly so the polling logic stays free of any HTTP coupling.
    """
    yield "retry: 3000\n\n"
    last_id = after
    while True:
        if await is_disconnected():
            return
        events = await anyio.to_thread.run_sync(_query_events, db, last_id, 50)
        for data in events:
            last_id = data["id"]
            yield f"data: {jsonlib.dumps(data)}\n\n"
        await anyio.sleep(1.0)


@router.get("/events/stream")
async def live_activity_stream(
    request: Request,
    after: int = 0,
    user: User = Depends(require_admin),
):
    """Server-Sent Events: pushes new user_events as they are ingested.

    Polling is cheap (indexed PK range scan) and the feed is admin-only, so a
    simple 1s poll is acceptable; a Postgres NOTIFY/LISTEN path could replace
    it if traffic grows.
    """

    async def _disconnected() -> bool:
        try:
            with anyio.fail_after(0.15):
                return await request.is_disconnected()
        except TimeoutError:
            return False

    async def event_gen():
        from app.database import SessionLocal

        db = SessionLocal()
        try:
            async for chunk in _event_stream(db, after, _disconnected):
                yield chunk
        finally:
            db.close()

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# Users management
# ---------------------------------------------------------------------------
@router.get("/users", response_class=HTMLResponse)
def admin_users(
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
    q: str = "",
    page: int = 1,
):
    per_page = 20
    query = db.query(User)
    if q.strip():
        like = f"%{q.strip()}%"
        query = query.filter((User.email.ilike(like)) | (User.full_name.ilike(like)))
    total = query.count()
    from math import ceil

    pages = max(1, ceil(total / per_page))
    page = max(1, min(page, pages))
    users = (
        query.order_by(User.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )
    return templates.TemplateResponse(
        request,
        "admin/users.html",
        {
            "current_user": user,
            "users": users,
            "total": total,
            "page": page,
            "pages": pages,
            "q": q,
        },
    )


@router.get("/users/new", response_class=HTMLResponse)
def new_user_form(
    request: Request,
    user: User = Depends(require_admin),
):
    return templates.TemplateResponse(
        request,
        "admin/user_form.html",
        {
            "current_user": user,
            "target": None,
            "error": None,
            "errors": None,
            "form": _user_form_values(),
        },
    )


@router.post("/users", response_class=HTMLResponse)
def create_user(
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
    full_name: str = Form(""),
    email: str = Form(""),
    mobile: str = Form(""),
    age: str = Form(""),
    gender: str = Form(""),
    role: str = Form("user"),
    telegram_chat_id: str = Form(""),
    new_password: str = Form(""),
):
    data, errors = _validate_user_data(
        db, email, full_name, mobile, age, gender, role, telegram_chat_id, new_password,
        target=None, require_password=True,
    )
    if errors:
        return templates.TemplateResponse(
            request,
            "admin/user_form.html",
            {
                "current_user": user,
                "target": None,
                "error": next(iter(errors.values())),
                "errors": errors,
                "form": _user_form_values(raw=locals()),
            },
            status_code=400,
        )
    new_user = User(
        email=data["email"],
        password_hash=auth_service.hash_password(data["new_password"]),
        full_name=data["full_name"],
        role=data["role"],
        mobile=data["mobile"],
        age=data["age"],
        gender=data["gender"],
        telegram_chat_id=data["telegram_chat_id"] or None,
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    response = RedirectResponse(url="/admin/users", status_code=303)
    set_flash(response, f"User created: {new_user.full_name or new_user.email}", "success")
    return response


@router.get("/users/{user_id}/edit", response_class=HTMLResponse)
def edit_user_form(
    user_id: int,
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found.")
    return templates.TemplateResponse(
        request,
        "admin/user_form.html",
        {
            "current_user": user,
            "target": target,
            "error": None,
            "errors": None,
            "form": _user_form_values(target),
        },
    )


def _user_form_values(target: User | None = None, raw: dict | None = None) -> dict:
    if raw is not None:
        return {
            "full_name": raw.get("full_name", ""),
            "email": raw.get("email", ""),
            "mobile": raw.get("mobile", ""),
            "age": raw.get("age", ""),
            "gender": raw.get("gender", ""),
            "role": raw.get("role", "user"),
            "telegram_chat_id": raw.get("telegram_chat_id", ""),
            "new_password": raw.get("new_password", ""),
        }
    if target is None:
        return {
            "full_name": "", "email": "", "mobile": "", "age": "", "gender": "",
            "role": "user", "telegram_chat_id": "", "new_password": "",
        }
    return {
        "full_name": target.full_name or "",
        "email": target.email or "",
        "mobile": target.mobile or "",
        "age": str(target.age) if target.age is not None else "",
        "gender": target.gender or "",
        "role": target.role or "user",
        "telegram_chat_id": target.telegram_chat_id or "",
        "new_password": "",
    }


def _validate_user_data(
    db: Session,
    email: str,
    full_name: str,
    mobile: str,
    age: str,
    gender: str,
    role: str,
    telegram_chat_id: str,
    new_password: str,
    target: User | None = None,
    require_password: bool = False,
) -> tuple[dict, dict]:
    import re

    errors: dict[str, str] = {}
    data: dict = {
        "email": email.strip().lower(),
        "full_name": full_name.strip(),
        "mobile": mobile.strip(),
        "gender": gender.strip(),
        "role": role.strip(),
        "telegram_chat_id": telegram_chat_id.strip(),
        "age": None,
    }

    if len(data["full_name"]) < 2:
        errors["full_name"] = "Please enter a valid full name."
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", data["email"]):
        errors["email"] = "Please enter a valid email address."
    else:
        existing = auth_service.get_user_by_email(db, data["email"])
        if existing is not None and (target is None or existing.id != target.id):
            errors["email"] = "An account with this email already exists."
    digits_only = re.sub(r"[^\d]", "", data["mobile"])
    if not (7 <= len(digits_only) <= 15):
        errors["mobile"] = "Enter a valid mobile number (7–15 digits)."
    try:
        age_val = int(age)
    except (TypeError, ValueError):
        errors["age"] = "Please enter a valid age."
    else:
        if not (1 <= age_val <= 119):
            errors["age"] = "Please enter an age between 1 and 119."
        else:
            data["age"] = age_val
    if not data["gender"]:
        errors["gender"] = "Please select a gender."
    if data["role"] not in ("user", "admin"):
        errors["role"] = "Role must be user or admin."
    if require_password and not new_password:
        errors["new_password"] = "Password is required."
    elif new_password and len(new_password) < 8:
        errors["new_password"] = "Password must be at least 8 characters."
    elif new_password:
        data["new_password"] = new_password

    return data, errors


@router.post("/users/{user_id}", response_class=HTMLResponse)
def update_user(
    user_id: int,
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
    full_name: str = Form(""),
    email: str = Form(""),
    mobile: str = Form(""),
    age: str = Form(""),
    gender: str = Form(""),
    role: str = Form("user"),
    telegram_chat_id: str = Form(""),
    new_password: str = Form(""),
):
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found.")
    data, errors = _validate_user_data(
        db, email, full_name, mobile, age, gender, role, telegram_chat_id, new_password, target
    )
    if errors:
        return templates.TemplateResponse(
            request,
            "admin/user_form.html",
            {
                "current_user": user,
                "target": target,
                "error": next(iter(errors.values())),
                "errors": errors,
                "form": _user_form_values(raw=locals()),
            },
            status_code=400,
        )
    target.full_name = data["full_name"]
    target.email = data["email"]
    target.mobile = data["mobile"]
    target.age = data["age"]
    target.gender = data["gender"]
    target.role = data["role"]
    target.telegram_chat_id = data["telegram_chat_id"] or None
    if data.get("new_password"):
        target.password_hash = auth_service.hash_password(data["new_password"])
    db.commit()
    db.refresh(target)
    response = RedirectResponse(url="/admin/users", status_code=303)
    set_flash(response, f"User updated: {target.full_name or target.email}", "success")
    return response


@router.post("/users/{user_id}/delete", response_class=HTMLResponse)
def delete_user(
    user_id: int,
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found.")
    if target.id == user.id:
        raise HTTPException(status_code=400, detail="You can't delete your own account.")
    email = target.email
    db.delete(target)
    db.commit()
    response = RedirectResponse(url="/admin/users", status_code=303)
    set_flash(response, f"User deleted: {email}", "success")
    return response
