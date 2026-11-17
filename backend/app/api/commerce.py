"""Commerce plane API.

Commerce owns the customer-facing transaction: POS integration, orders and
transactions, pricing and promotions, loyalty, eCommerce and customer-facing
workflows (receipts, cancellation).

The server computes all pricing from the price book plus active promotions;
client-supplied prices are a fallback for lines with no price book entry,
never an override of one.
"""

import uuid
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import settings
from app.db.database import get_db
from app.planes.commerce import (
    CommerceError,
    place_order,
    price_line,
    restock_lines,
    reverse_points,
)
from app.planes.models import (
    CommerceOrder,
    CommerceOrderLine,
    LoyaltyMember,
    ProductPrice,
    Promotion,
)
from app.planes.operations import OperationsError, OptimisticConcurrencyError, cas_update

router = APIRouter(prefix="/api/v1/commerce", tags=["commerce"])


# ------------------------------------------------------------------ schemas

class PriceCreate(BaseModel):
    sku: str = Field(..., min_length=1)
    unit_price: float = Field(..., ge=0)
    store_id: Optional[str] = None
    currency: str = "USD"
    valid_from: Optional[datetime] = None
    valid_to: Optional[datetime] = None


class PromotionCreate(BaseModel):
    code: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1)
    kind: str = Field(..., pattern="^(percent_off|amount_off)$")
    value: float = Field(..., ge=0)
    sku: Optional[str] = None
    channel: Optional[str] = None
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None


class LoyaltyMemberCreate(BaseModel):
    member_number: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1)
    email: Optional[str] = None


class OrderLineIn(BaseModel):
    sku: str = Field(..., min_length=1)
    quantity: int = Field(..., ge=1)
    unit_price: Optional[float] = Field(None, ge=0)  # fallback only; price book wins


class OrderCreate(BaseModel):
    channel: str = Field("pos", pattern="^(pos|ecommerce|mobile|kiosk)$")
    store_id: Optional[str] = None
    register_id: Optional[str] = None
    payment_method: Optional[str] = None
    member_number: Optional[str] = None
    client_order_id: Optional[str] = Field(None, max_length=128)
    lines: List[OrderLineIn] = Field(..., min_length=1)


class CancelOrder(BaseModel):
    actor: str = Field(..., min_length=1)
    reason: str = Field(..., min_length=1)


# ------------------------------------------------------------- price book

@router.post("/prices", status_code=201)
def create_price(payload: PriceCreate, db: Session = Depends(get_db)):
    price = ProductPrice(
        sku=payload.sku,
        store_id=payload.store_id,
        unit_price=payload.unit_price,
        currency=payload.currency,
        valid_from=payload.valid_from or datetime.now(timezone.utc),
        valid_to=payload.valid_to,
        active=True,
    )
    db.add(price)
    db.commit()
    db.refresh(price)
    return _price_out(price)


@router.get("/prices")
def list_prices(sku: Optional[str] = None, store_id: Optional[str] = None, db: Session = Depends(get_db)):
    query = db.query(ProductPrice).filter(ProductPrice.active.is_(True))
    if sku:
        query = query.filter(ProductPrice.sku == sku)
    if store_id:
        query = query.filter((ProductPrice.store_id == store_id) | (ProductPrice.store_id.is_(None)))
    return {"prices": [_price_out(p) for p in query.order_by(ProductPrice.sku.asc()).all()]}


@router.get("/prices/evaluate")
def evaluate_price(
    sku: str = Query(...),
    quantity: int = Query(1, ge=1),
    channel: str = Query("pos", pattern="^(pos|ecommerce|mobile|kiosk)$"),
    store_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    try:
        return price_line(db, sku=sku, quantity=quantity, channel=channel, store_id=store_id or settings.STORE_ID)
    except CommerceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)


# ------------------------------------------------------------- promotions

@router.post("/promotions", status_code=201)
def create_promotion(payload: PromotionCreate, db: Session = Depends(get_db)):
    promo = Promotion(**payload.model_dump(), active=True)
    db.add(promo)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=409, detail=f"Promotion code {payload.code!r} already exists")
    db.refresh(promo)
    return _promo_out(promo)


@router.get("/promotions")
def list_promotions(active_only: bool = True, db: Session = Depends(get_db)):
    query = db.query(Promotion)
    if active_only:
        query = query.filter(Promotion.active.is_(True))
    return {"promotions": [_promo_out(p) for p in query.order_by(Promotion.code.asc()).all()]}


# ---------------------------------------------------------------- loyalty

@router.post("/loyalty/members", status_code=201)
def enroll_loyalty_member(payload: LoyaltyMemberCreate, db: Session = Depends(get_db)):
    existing = db.query(LoyaltyMember).filter(LoyaltyMember.member_number == payload.member_number).first()
    if existing:
        raise HTTPException(status_code=409, detail="Member number already enrolled")
    member = LoyaltyMember(**payload.model_dump(), points=0, tier="STANDARD")
    db.add(member)
    db.commit()
    db.refresh(member)
    return _member_out(member)


@router.get("/loyalty/members/{member_number}")
def get_loyalty_member(member_number: str, db: Session = Depends(get_db)):
    member = db.query(LoyaltyMember).filter(LoyaltyMember.member_number == member_number).first()
    if member is None:
        raise HTTPException(status_code=404, detail="Loyalty member not found")
    return _member_out(member)


# ----------------------------------------------------------------- orders

@router.post("/orders", status_code=201)
def place_order_endpoint(payload: OrderCreate, response: Response, db: Session = Depends(get_db)):
    store_id = payload.store_id or settings.STORE_ID
    order, created = _place(payload, db, store_id)
    if not created:
        response.status_code = 200
    return _order_detail(db, order)


@router.get("/orders")
def list_orders(
    channel: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    query = db.query(CommerceOrder)
    if channel:
        query = query.filter(CommerceOrder.channel == channel)
    if status:
        query = query.filter(CommerceOrder.status == status)
    orders = query.order_by(CommerceOrder.id.desc()).limit(limit).all()
    return {"orders": [_order_detail(db, o, lines=False) for o in orders]}


@router.get("/orders/{order_ref}")
def get_order(order_ref: str, db: Session = Depends(get_db)):
    return _order_detail(db, _find_order(db, order_ref))


@router.get("/orders/{order_ref}/receipt")
def get_receipt(order_ref: str, db: Session = Depends(get_db)):
    """Customer-facing receipt payload."""
    order = _find_order(db, order_ref)
    detail = _order_detail(db, order)
    return {
        "store_id": order.store_id,
        "order_ref": order.order_ref,
        "channel": order.channel,
        "status": order.status,
        "lines": detail["lines"],
        "subtotal": order.subtotal,
        "discount_total": order.discount_total,
        "total": order.total,
        "points_earned": order.points_earned,
        "member_number": order.member_number,
    }


@router.post("/orders/{order_ref}/cancel")
def cancel_order(order_ref: str, payload: CancelOrder, db: Session = Depends(get_db)):
    order = _find_order(db, order_ref)
    if order.status == "CANCELLED":
        raise HTTPException(status_code=409, detail="Order already cancelled")
    if order.status not in ("PAID", "PLACED"):
        raise HTTPException(status_code=409, detail=f"Order in status {order.status} cannot be cancelled")

    lines = [
        {"sku": line.sku, "quantity": line.quantity}
        for line in db.query(CommerceOrderLine).filter(CommerceOrderLine.order_id == order.id).all()
    ]
    try:
        restock_lines(db, lines=lines, store_id=order.store_id, order_ref=order.order_ref, actor=payload.actor)
    except OperationsError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)

    if order.member_number and order.points_earned:
        member = db.query(LoyaltyMember).filter(LoyaltyMember.member_number == order.member_number).first()
        if member:
            reverse_points(db, member, order.points_earned)

    try:
        cas_update(db, CommerceOrder, order.id, order.version, {
            "status": "CANCELLED",
            "cancelled_at": datetime.now(timezone.utc),
            "cancel_reason": payload.reason,
        })
    except OptimisticConcurrencyError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    db.commit()
    db.refresh(order)
    return _order_detail(db, order)


# -------------------------------------------------- POS / eCommerce entry

@router.post("/pos/transactions", status_code=201)
def post_pos_transaction(payload: OrderCreate, response: Response, db: Session = Depends(get_db)):
    """POS integration entry point.

    Stable contract for point-of-sale lanes: POST a basket with register_id
    and payment_method; the response is the committed, priced transaction.
    Unit pricing always comes from the price book. Idempotent on
    client_order_id (replays return the original order with 200).
    """
    payload.channel = "pos"
    return _channel_order(payload, response, db)


@router.post("/ecommerce/orders", status_code=201)
def post_ecommerce_order(payload: OrderCreate, response: Response, db: Session = Depends(get_db)):
    """eCommerce entry point: same transaction rail as POS, channel='ecommerce'."""
    payload.channel = "ecommerce"
    return _channel_order(payload, response, db)


@router.get("/pos/status")
def pos_status():
    return {
        "integration": "direct-post",
        "endpoint": "/api/v1/commerce/pos/transactions",
        "auth": "network-level (bind POS lane to the commerce service); asserted identity via register_id",
        "idempotency": "client_order_id is the deduplication key; replays return the original order",
        "channels": ["pos", "ecommerce", "mobile", "kiosk"],
    }


# ------------------------------------------------------------------ helpers

def _channel_order(payload: OrderCreate, response: Response, db: Session):
    store_id = payload.store_id or settings.STORE_ID
    order, created = _place(payload, db, store_id)
    if not created:
        response.status_code = 200
    return _order_detail(db, order)


def _place(payload: OrderCreate, db: Session, store_id: str):
    try:
        return place_order(
            db,
            channel=payload.channel,
            store_id=store_id,
            register_id=payload.register_id,
            payment_method=payload.payment_method,
            member_number=payload.member_number,
            client_order_id=payload.client_order_id,
            lines=[line.model_dump() for line in payload.lines],
        )
    except CommerceError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    except OperationsError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)


def _find_order(db: Session, order_ref: str) -> CommerceOrder:
    order = db.query(CommerceOrder).filter(CommerceOrder.order_ref == order_ref).first()
    if order is None:
        raise HTTPException(status_code=404, detail=f"Order {order_ref} not found")
    return order


def _price_out(p: ProductPrice) -> dict:
    return {
        "id": p.id,
        "sku": p.sku,
        "store_id": p.store_id,
        "unit_price": p.unit_price,
        "currency": p.currency,
        "valid_from": p.valid_from.isoformat() if p.valid_from else None,
        "valid_to": p.valid_to.isoformat() if p.valid_to else None,
    }


def _promo_out(p: Promotion) -> dict:
    return {
        "id": p.id,
        "code": p.code,
        "name": p.name,
        "kind": p.kind,
        "value": p.value,
        "sku": p.sku,
        "channel": p.channel,
        "starts_at": p.starts_at.isoformat() if p.starts_at else None,
        "ends_at": p.ends_at.isoformat() if p.ends_at else None,
        "active": p.active,
    }


def _member_out(m: LoyaltyMember) -> dict:
    return {
        "member_number": m.member_number,
        "name": m.name,
        "email": m.email,
        "points": m.points,
        "tier": m.tier,
    }


def _order_detail(db: Session, order: CommerceOrder, lines: bool = True) -> dict:
    detail = {
        "order_ref": order.order_ref,
        "client_order_id": order.client_order_id,
        "channel": order.channel,
        "store_id": order.store_id,
        "register_id": order.register_id,
        "payment_method": order.payment_method,
        "member_number": order.member_number,
        "status": order.status,
        "subtotal": order.subtotal,
        "discount_total": order.discount_total,
        "total": order.total,
        "points_earned": order.points_earned,
        "created_at": order.created_at.isoformat() if order.created_at else None,
    }
    if lines:
        detail["lines"] = [
            {
                "sku": line.sku,
                "quantity": line.quantity,
                "unit_price": line.unit_price,
                "promo_code": line.promo_code,
                "discount": line.discount,
                "line_total": line.line_total,
            }
            for line in db.query(CommerceOrderLine).filter(CommerceOrderLine.order_id == order.id).all()
        ]
    return detail
