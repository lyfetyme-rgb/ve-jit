"""Persistence for the three planes.

All plane tables live beside the baseline `inventory_items` table and are
created by the same `Base.metadata.create_all` call at startup. Existing
tables are never altered by this module.
"""

from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Float, Integer, String, Text
from sqlalchemy.sql import func

from app.db.database import Base


def utcnow():
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------- commerce

class ProductPrice(Base):
    __tablename__ = "product_prices"

    id = Column(Integer, primary_key=True, index=True)
    sku = Column(String(100), index=True, nullable=False)
    store_id = Column(String(100), nullable=True, index=True)  # NULL = chain-wide
    unit_price = Column(Float, nullable=False)
    currency = Column(String(8), nullable=False, default="USD")
    valid_from = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    valid_to = Column(DateTime(timezone=True), nullable=True)
    active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Promotion(Base):
    __tablename__ = "promotions"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(64), unique=True, index=True, nullable=False)
    name = Column(String(255), nullable=False)
    kind = Column(String(32), nullable=False)  # percent_off | amount_off
    value = Column(Float, nullable=False)
    sku = Column(String(100), nullable=True, index=True)  # NULL = basket-wide
    channel = Column(String(32), nullable=True)  # NULL = all channels
    starts_at = Column(DateTime(timezone=True), nullable=True)
    ends_at = Column(DateTime(timezone=True), nullable=True)
    active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class LoyaltyMember(Base):
    __tablename__ = "loyalty_members"

    id = Column(Integer, primary_key=True, index=True)
    member_number = Column(String(64), unique=True, index=True, nullable=False)
    version = Column(Integer, nullable=False, server_default="1", default=1)
    name = Column(String(255), nullable=False)
    email = Column(String(255), nullable=True)
    points = Column(Integer, nullable=False, default=0)
    tier = Column(String(32), nullable=False, default="STANDARD")
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class CommerceOrder(Base):
    __tablename__ = "commerce_orders"

    id = Column(Integer, primary_key=True, index=True)
    order_ref = Column(String(64), unique=True, index=True, nullable=False)
    version = Column(Integer, nullable=False, server_default="1", default=1)
    client_order_id = Column(String(128), unique=True, nullable=True)  # idempotency key
    channel = Column(String(32), nullable=False, default="pos")  # pos|ecommerce|mobile|kiosk
    store_id = Column(String(100), nullable=False, index=True)
    register_id = Column(String(64), nullable=True)
    payment_method = Column(String(64), nullable=True)
    member_number = Column(String(64), nullable=True, index=True)
    status = Column(String(32), nullable=False, default="PAID", index=True)  # PLACED|PAID|FULFILLED|CANCELLED
    subtotal = Column(Float, nullable=False, default=0.0)
    discount_total = Column(Float, nullable=False, default=0.0)
    total = Column(Float, nullable=False, default=0.0)
    points_earned = Column(Integer, nullable=False, default=0)
    cancelled_at = Column(DateTime(timezone=True), nullable=True)
    cancel_reason = Column(String(512), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class CommerceOrderLine(Base):
    __tablename__ = "commerce_order_lines"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("commerce_orders.id"), index=True, nullable=False)
    sku = Column(String(100), index=True, nullable=False)
    name = Column(String(255), nullable=True)
    quantity = Column(Integer, nullable=False)
    unit_price = Column(Float, nullable=False)
    promo_code = Column(String(64), nullable=True)
    discount = Column(Float, nullable=False, default=0.0)
    line_total = Column(Float, nullable=False)


# -------------------------------------------------------------- operations

class InventoryLedgerEntry(Base):
    __tablename__ = "inventory_ledger"

    id = Column(Integer, primary_key=True, index=True)
    sku = Column(String(100), index=True, nullable=False)
    store_id = Column(String(100), index=True, nullable=False)
    delta = Column(Integer, nullable=False)  # signed
    reason = Column(String(32), index=True, nullable=False)  # SALE|RECEIPT|ADJUSTMENT|TRANSFER_IN|TRANSFER_OUT|CYCLE_COUNT|WASTE|RETURN
    ref = Column(String(128), nullable=True, index=True)
    actor = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Supplier(Base):
    __tablename__ = "suppliers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), unique=True, nullable=False)
    contact_email = Column(String(255), nullable=True)
    payment_terms = Column(String(32), nullable=False, default="NET30")
    lead_time_days = Column(Integer, nullable=False, default=7)
    sla_otif_pct = Column(Float, nullable=False, default=95.0)
    sla_fill_rate_pct = Column(Float, nullable=False, default=98.0)
    active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"

    id = Column(Integer, primary_key=True, index=True)
    po_number = Column(String(64), unique=True, index=True, nullable=False)
    version = Column(Integer, nullable=False, server_default="1", default=1)
    supplier_id = Column(Integer, ForeignKey("suppliers.id"), index=True, nullable=False)
    store_id = Column(String(100), nullable=False)
    status = Column(String(32), nullable=False, default="DRAFT", index=True)  # DRAFT|APPROVED|PARTIALLY_RECEIVED|RECEIVED|CANCELLED
    created_by = Column(String(255), nullable=False)
    approved_by = Column(String(255), nullable=True)
    expected_at = Column(DateTime(timezone=True), nullable=True)
    received_at = Column(DateTime(timezone=True), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class PurchaseOrderLine(Base):
    __tablename__ = "purchase_order_lines"

    id = Column(Integer, primary_key=True, index=True)
    po_id = Column(Integer, ForeignKey("purchase_orders.id"), index=True, nullable=False)
    sku = Column(String(100), index=True, nullable=False)
    quantity_ordered = Column(Integer, nullable=False)
    quantity_received = Column(Integer, nullable=False, default=0)
    unit_cost = Column(Float, nullable=False, default=0.0)


class TransferOrder(Base):
    __tablename__ = "transfer_orders"

    id = Column(Integer, primary_key=True, index=True)
    transfer_ref = Column(String(64), unique=True, index=True, nullable=False)
    version = Column(Integer, nullable=False, server_default="1", default=1)
    from_store = Column(String(100), nullable=False)
    to_store = Column(String(100), nullable=False)
    sku = Column(String(100), index=True, nullable=False)
    quantity = Column(Integer, nullable=False)
    status = Column(String(32), nullable=False, default="REQUESTED", index=True)  # REQUESTED|SHIPPED|RECEIVED|CANCELLED
    created_by = Column(String(255), nullable=False)
    shipped_by = Column(String(255), nullable=True)
    received_by = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    shipped_at = Column(DateTime(timezone=True), nullable=True)
    received_at = Column(DateTime(timezone=True), nullable=True)


class VendorMessage(Base):
    __tablename__ = "vendor_messages"

    id = Column(Integer, primary_key=True, index=True)
    supplier_id = Column(Integer, ForeignKey("suppliers.id"), index=True, nullable=False)
    po_number = Column(String(64), nullable=True, index=True)
    direction = Column(String(16), nullable=False)  # TO_VENDOR | FROM_VENDOR
    subject = Column(String(255), nullable=False)
    body = Column(Text, nullable=False)
    author = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class WasteRecord(Base):
    __tablename__ = "waste_records"

    id = Column(Integer, primary_key=True, index=True)
    sku = Column(String(100), index=True, nullable=False)
    store_id = Column(String(100), index=True, nullable=False)
    quantity = Column(Integer, nullable=False)
    unit = Column(String(32), nullable=False, default="unit")
    reason = Column(String(32), index=True, nullable=False)  # SPOILAGE|EXPIRED|DAMAGE|SHRINK|OTHER
    estimated_cost = Column(Float, nullable=False, default=0.0)
    recorded_by = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


# --------------------------------------------------------    intelligence

class Recommendation(Base):
    """Intelligence output. Never an execution instruction on its own.

    Lifecycle: PROPOSED -> APPROVED -> EXECUTED (by Operations)
                        -> REJECTED
                        -> EXPIRED (reserved)
    """

    __tablename__ = "recommendations"

    id = Column(Integer, primary_key=True, index=True)
    uid = Column(String(64), unique=True, index=True, nullable=False)
    version = Column(Integer, nullable=False, server_default="1", default=1)
    plane = Column(String(32), nullable=False, default="intelligence")
    kind = Column(String(64), index=True, nullable=False)  # REPLENISHMENT|TRANSFER|WASTE_REVIEW|WORKFORCE_GUIDANCE|...
    subject_type = Column(String(64), nullable=False)  # sku | supplier | store | order
    subject_id = Column(String(128), nullable=False, index=True)
    summary = Column(String(512), nullable=False)
    rationale = Column(Text, nullable=True)
    confidence = Column(Float, nullable=False, default=0.5)
    recommended_action = Column(Text, nullable=True)  # JSON payload for the executing plane
    status = Column(String(32), nullable=False, default="PROPOSED", index=True)
    created_by = Column(String(255), nullable=False, default="intelligence-engine")
    approved_by = Column(String(255), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    executed_by = Column(String(255), nullable=True)
    executed_at = Column(DateTime(timezone=True), nullable=True)
    execution_ref = Column(String(128), nullable=True)
    note = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


RECOMMENDATION_STATUSES = ("PROPOSED", "APPROVED", "REJECTED", "EXECUTED", "EXPIRED")
