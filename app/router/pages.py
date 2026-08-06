from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import current_user
from app.models import Product, User
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
        "now": datetime.now(timezone.utc),
    }


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
        .limit(48)
        .all()
    )
    ctx = _nav_context(request, user, db)
    ctx.update(
        {
            "current_user": user,
            "products": products,
            "title": "Shop · SmartReco",
        }
    )
    return templates.TemplateResponse(request, "index.html", ctx)


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
    products = (
        product_service.search_products(db, q, category=category or None) if q else []
    )
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


@router.post("/recommendations/refresh")
def refresh_recommendations(
    request: Request,
    user: User | None = Depends(current_user),
    db: Session = Depends(get_db),
):
    if user is None:
        return RedirectResponse(url="/auth/login", status_code=303)
    rec_service.refresh(db, user)
    return RedirectResponse(url="/recommendations", status_code=303)