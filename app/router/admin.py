from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import require_admin
from app.models import Product, User
from app.services import products as product_service
from app.templating import templates

router = APIRouter(prefix="/admin")


def _parse_tags(raw: str) -> list[str]:
    return [t.strip() for t in raw.replace(",", " ").split() if t.strip()]


def _form_data(
    title: str,
    description: str,
    category: str,
    price: str,
    level: str,
    image_url: str,
    tags: str,
) -> dict:
    return {
        "title": title.strip(),
        "description": description,
        "category": category.strip(),
        "price": price,
        "level": level.strip(),
        "image_url": image_url.strip(),
        "tags": _parse_tags(tags),
    }


@router.get("/products", response_class=HTMLResponse)
def admin_products(
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    products = product_service.list_products(db, include_inactive=True)
    return templates.TemplateResponse(
        request,
        "admin/product_list.html",
        {"current_user": user, "products": products},
    )


@router.get("/products/new", response_class=HTMLResponse)
def new_product_form(request: Request, user: User = Depends(require_admin)):
    return templates.TemplateResponse(
        request,
        "admin/product_form.html",
        {"current_user": user, "product": None, "error": None},
    )


@router.post("/products", response_class=HTMLResponse)
def create_product(
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
    title: str = Form(...),
    description: str = Form(""),
    category: str = Form(...),
    price: str = Form(...),
    level: str = Form(""),
    image_url: str = Form(""),
    tags: str = Form(""),
):
    if not title.strip() or not category.strip():
        return templates.TemplateResponse(
            request,
            "admin/product_form.html",
            {"current_user": user, "product": None, "error": "Title and category are required."},
            status_code=400,
        )
    try:
        product = product_service.create_product(
            db, _form_data(title, description, category, price, level, image_url, tags)
        )
    except ValueError:
        return templates.TemplateResponse(
            request,
            "admin/product_form.html",
            {"current_user": user, "product": None, "error": "Price must be a number."},
            status_code=400,
        )
    return RedirectResponse(url=f"/admin/products/{product.id}/edit", status_code=303)


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
        {"current_user": user, "product": product, "error": None},
    )


@router.post("/products/{product_id}", response_class=HTMLResponse)
def update_product(
    product_id: int,
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
    title: str = Form(...),
    description: str = Form(""),
    category: str = Form(...),
    price: str = Form(...),
    level: str = Form(""),
    image_url: str = Form(""),
    tags: str = Form(""),
):
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found.")
    if not title.strip() or not category.strip():
        return templates.TemplateResponse(
            request,
            "admin/product_form.html",
            {"current_user": user, "product": product, "error": "Title and category are required."},
            status_code=400,
        )
    try:
        product_service.update_product(
            db, product, _form_data(title, description, category, price, level, image_url, tags)
        )
    except ValueError:
        return templates.TemplateResponse(
            request,
            "admin/product_form.html",
            {"current_user": user, "product": product, "error": "Price must be a number."},
            status_code=400,
        )
    return RedirectResponse(url="/admin/products", status_code=303)


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
    return RedirectResponse(url="/admin/products", status_code=303)


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
    return RedirectResponse(url="/admin/products", status_code=303)
