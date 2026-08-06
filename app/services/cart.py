"""Cart service.

A cart is keyed either by ``user_id`` (logged-in shopper) or by a guest
``session_key`` (anonymous browser). Logged-in carts always win: when a user
logs in, any guest cart is merged into the user cart and the guest lines are
deleted.
"""
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import CartItem, Product


def _query(db: Session, user, session_key: str | None):
    q = db.query(CartItem)
    if user is not None:
        return q.filter(CartItem.user_id == user.id)
    return q.filter(CartItem.session_key == session_key)


def count_items(db: Session, user, session_key: str | None) -> int:
    if user is None and not session_key:
        return 0
    total = 0
    for row in _query(db, user, session_key).all():
        total += row.quantity
    return total


def _set_line(db: Session, user, session_key: str | None, product_id: int, quantity: int):
    row = _query(db, user, session_key).filter(CartItem.product_id == product_id).first()
    if row is None:
        db.add(
            CartItem(
                user_id=user.id if user is not None else None,
                session_key=None if user is not None else session_key,
                product_id=product_id,
                quantity=quantity,
            )
        )
    else:
        row.quantity = quantity
    db.commit()


def add_item(db: Session, user, session_key: str | None, product_id: int, quantity: int = 1) -> int:
    if db.get(Product, product_id) is None:
        return 0
    row = _query(db, user, session_key).filter(CartItem.product_id == product_id).first()
    new_qty = min((row.quantity if row else 0) + quantity, 99)
    _set_line(db, user, session_key, product_id, new_qty)
    return count_items(db, user, session_key)


def set_quantity(db: Session, user, session_key: str | None, product_id: int, quantity: int) -> int:
    if quantity <= 0:
        return remove_item(db, user, session_key, product_id)
    _set_line(db, user, session_key, product_id, quantity)
    return count_items(db, user, session_key)


def remove_item(db: Session, user, session_key: str | None, product_id: int) -> int:
    row = _query(db, user, session_key).filter(CartItem.product_id == product_id).first()
    if row is not None:
        db.delete(row)
        db.commit()
    return count_items(db, user, session_key)


def get_cart(db: Session, user, session_key: str | None) -> dict:
    """Return the full cart: lines (with product data), count and subtotal."""
    lines = []
    subtotal = Decimal("0.00")
    rows = _query(db, user, session_key).all()
    for row in rows:
        product = db.get(Product, row.product_id)
        if product is None or not product.is_active:
            continue
        line_total = Decimal(str(product.price)) * row.quantity
        subtotal += line_total
        lines.append(
            {
                "product_id": product.id,
                "title": product.title,
                "slug": product.slug,
                "category": product.category,
                "image_url": product.image_url,
                "unit_price": float(product.price),
                "quantity": row.quantity,
                "line_total": float(line_total),
                "product_url": product.product_url,
            }
        )
    return {
        "lines": lines,
        "count": sum(line["quantity"] for line in lines),
        "subtotal": float(subtotal),
    }


def merge_guest_into_user(db: Session, user, guest_session_key: str | None) -> int:
    """Move a guest cart into the user cart (quantities add up)."""
    if not guest_session_key:
        return 0
    guest_rows = (
        db.query(CartItem)
        .filter(CartItem.session_key == guest_session_key, CartItem.user_id.is_(None))
        .all()
    )
    for row in guest_rows:
        user_row = (
            db.query(CartItem)
            .filter(CartItem.user_id == user.id, CartItem.product_id == row.product_id)
            .first()
        )
        if user_row is not None:
            user_row.quantity = min(user_row.quantity + row.quantity, 99)
            db.delete(row)
        else:
            row.user_id = user.id
            row.session_key = None
    db.commit()
    return count_items(db, user, None)


def checkout(db: Session, user, session_key: str | None) -> dict:
    """Checkout: returns the purchased lines (for order confirmation), keeps
    the cart in place so the demo stays reversible."""
    cart = get_cart(db, user, session_key)
    return cart
