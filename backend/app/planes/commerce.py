"""Commerce plane domain services.

Commerce owns the customer-facing transaction end to end: POS integration,
orders and transactions, pricing and promotions, loyalty, eCommerce and the
customer-facing workflows around them (receipts, cancellation).

Pricing rule: the server is the only authority on what a basket costs. Each
line resolves its base price from the active price book (store-specific, else
chain-wide), applies the single best active promotion for that line, and the
order total is the sum of discounted lines. Client-supplied prices are a
fallback for lines with no price book entry, never an override of one.
"""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.planes.models import CommerceOrder, CommerceOrderLine, LoyaltyMember, ProductPrice, Promotion
from app.planes.operations import OperationsError, apply_stock_movement

POINTS_PER_CURRENCY_UNIT = 1  # 1 point per whole currency unit paid
TIER_THRESHOLDS = (("GOLD", 1500), ("SILVER", 500), ("STANDARD", 0))


class CommerceError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def resolve_unit_price(db: Session, sku: str, store_id: Optional[str]) -> Optional[ProductPrice]:
    now = datetime.now(timezone.utc)
    valid = [
        price
        for price in db.query(ProductPrice).filter(ProductPrice.sku == sku, ProductPrice.active.is_(True)).all()
        if (price.valid_from is None or _aware(price.valid_from) <= now)
        and (price.valid_to is None or _aware(price.valid_to) >= now)
    ]
    store_specific = [p for p in valid if p.store_id == store_id] if store_id else []
    chain_wide = [p for p in valid if p.store_id is None]
    pool = store_specific or chain_wide
    if not pool:
        return None
    return max(pool, key=lambda p: _aware(p.valid_from) if p.valid_from else datetime.min.replace(tzinfo=timezone.utc))


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def best_promotion(db: Session, sku: str, channel: str, unit_price: float, quantity: int) -> Optional[Promotion]:
    now = datetime.now(timezone.utc)
    best: Optional[Promotion] = None
    best_discount = 0.0
    for promo in db.query(Promotion).filter(Promotion.active.is_(True)).all():
        if promo.sku is not None and promo.sku != sku:
            continue
        if promo.channel is not None and promo.channel != channel:
            continue
        if promo.starts_at is not None and _aware(promo.starts_at) > now:
            continue
        if promo.ends_at is not None and _aware(promo.ends_at) < now:
            continue
        discount = line_discount(promo, unit_price, quantity)
        if discount > best_discount:
            best, best_discount = promo, discount
    return best


def line_discount(promo: Promotion, unit_price: float, quantity: int) -> float:
    if promo.kind == "percent_off":
        return round(unit_price * quantity * max(0.0, promo.value) / 100.0, 2)
    if promo.kind == "amount_off":
        return round(min(max(0.0, promo.value) * quantity, unit_price * quantity), 2)
    return 0.0


def price_line(
    db: Session,
    *,
    sku: str,
    quantity: int,
    channel: str,
    store_id: Optional[str],
    override_unit_price: Optional[float] = None,
) -> dict:
    """Price one line. Returns the full breakdown the caller needs to persist."""
    if quantity <= 0:
        raise CommerceError(422, "Line quantity must be positive")

    price_row = resolve_unit_price(db, sku, store_id)
    if price_row is not None:
        base_unit_price = price_row.unit_price
        price_source = "price_book"
    elif override_unit_price is not None and override_unit_price >= 0:
        base_unit_price = float(override_unit_price)
        price_source = "request"
    else:
        raise CommerceError(422, f"No active price for SKU {sku} (and no unit_price fallback supplied)")

    promo = best_promotion(db, sku, channel, base_unit_price, quantity)
    discount = line_discount(promo, base_unit_price, quantity) if promo else 0.0
    gross = round(base_unit_price * quantity, 2)
    net = round(gross - discount, 2)
    return {
        "sku": sku,
        "quantity": quantity,
        "unit_price": base_unit_price,
        "price_source": price_source,
        "promo_code": promo.code if promo else None,
        "discount": discount,
        "gross": gross,
        "line_total": net,
    }


def tier_for_points(points: int) -> str:
    for tier, threshold in TIER_THRESHOLDS:
        if points >= threshold:
            return tier
    return "STANDARD"


def accrue_points(member: LoyaltyMember, total: float) -> int:
    earned = int(total * POINTS_PER_CURRENCY_UNIT)
    member.points += earned
    member.tier = tier_for_points(member.points)
    return earned


def reverse_points(member: LoyaltyMember, points: int) -> None:
    member.points = max(0, member.points - int(points))
    member.tier = tier_for_points(member.points)


def apply_sale_movements(db: Session, *, lines: list[dict], store_id: str, order_ref: str, actor: str) -> None:
    for line in lines:
        apply_stock_movement(
            db,
            sku=line["sku"],
            store_id=store_id,
            delta=-int(line["quantity"]),
            reason="SALE",
            actor=actor,
            ref=order_ref,
        )


def restock_lines(db: Session, *, lines: list[dict], store_id: str, order_ref: str, actor: str) -> None:
    for line in lines:
        apply_stock_movement(
            db,
            sku=line["sku"],
            store_id=store_id,
            delta=int(line["quantity"]),
            reason="RETURN",
            actor=actor,
            ref=order_ref,
        )


def place_order(
    db: Session,
    *,
    channel: str,
    store_id: str,
    register_id: Optional[str],
    payment_method: Optional[str],
    member_number: Optional[str],
    client_order_id: Optional[str],
    lines: list[dict],
) -> tuple[CommerceOrder, bool]:
    """Place and settle an order. Returns (order, created).

    Idempotent on client_order_id: a replay returns the original order with
    created=False instead of double-posting stock and points.
    """
    import uuid

    if client_order_id:
        existing = db.query(CommerceOrder).filter(CommerceOrder.client_order_id == client_order_id).first()
        if existing:
            return existing, False

    member = None
    if member_number:
        member = db.query(LoyaltyMember).filter(LoyaltyMember.member_number == member_number).first()
        if member is None:
            raise CommerceError(404, f"Loyalty member {member_number!r} not found")

    priced = [
        price_line(
            db,
            sku=line["sku"],
            quantity=int(line["quantity"]),
            channel=channel,
            store_id=store_id,
            override_unit_price=line.get("unit_price"),
        )
        for line in lines
    ]

    subtotal = round(sum(p["gross"] for p in priced), 2)
    discount_total = round(sum(p["discount"] for p in priced), 2)
    total = round(subtotal - discount_total, 2)
    order_ref = f"{channel.upper()}-{uuid.uuid4().hex[:12].upper()}"
    actor = register_id or channel

    order = CommerceOrder(
        order_ref=order_ref,
        client_order_id=client_order_id,
        channel=channel,
        store_id=store_id,
        register_id=register_id,
        payment_method=payment_method,
        member_number=member_number,
        status="PAID",
        subtotal=subtotal,
        discount_total=discount_total,
        total=total,
        points_earned=0,
    )
    apply_sale_movements(db, lines=priced, store_id=store_id, order_ref=order_ref, actor=actor)

    db.add(order)
    db.flush()
    for p in priced:
        db.add(
            CommerceOrderLine(
                order_id=order.id,
                sku=p["sku"],
                quantity=p["quantity"],
                unit_price=p["unit_price"],
                promo_code=p["promo_code"],
                discount=p["discount"],
                line_total=p["line_total"],
            )
        )

    if member is not None:
        order.points_earned = accrue_points(member, total)

    db.commit()
    db.refresh(order)
    return order, True


__all__ = [
    "CommerceError",
    "accrue_points",
    "apply_sale_movements",
    "best_promotion",
    "line_discount",
    "place_order",
    "price_line",
    "resolve_unit_price",
    "restock_lines",
    "reverse_points",
    "tier_for_points",
]
