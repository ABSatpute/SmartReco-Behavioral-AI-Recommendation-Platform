from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import current_user
from app.models import Product, User
from app.services import products as product_service
from app.templating import templates

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def home(
    request: Request,
    user: User | None = Depends(current_user),
    db: Session = Depends(get_db),
):
    products = (
        db.query(Product)
        .filter(Product.is_active.is_(True))
        .order_by(Product.created_at.desc())
        .all()
    )
    return templates.TemplateResponse(
        request,
        "index.html",
        {"current_user": user, "products": products},
    )


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
    return templates.TemplateResponse(
        request,
        "product_detail.html",
        {"current_user": user, "product": product},
    )


@router.get("/search", response_class=HTMLResponse)
def search_page(
    request: Request,
    q: str = "",
    user: User | None = Depends(current_user),
    db: Session = Depends(get_db),
):
    products = product_service.search_products(db, q) if q else []
    return templates.TemplateResponse(
        request,
        "search.html",
        {"current_user": user, "query": q, "products": products},
    )


@router.get("/recommendations", response_class=HTMLResponse)
def recommendations_page(
    request: Request,
    user: User | None = Depends(current_user),
    db: Session = Depends(get_db),
):
    if user is None:
        return RedirectResponse(url="/auth/login", status_code=303)
    return templates.TemplateResponse(
        request,
        "recommendations.html",
        {"current_user": user},
    )
