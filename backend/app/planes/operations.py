"""Operations plane domain services.

Operations owns the physical business processes: inventory ledger, purchasing,
receiving, replenishment, DC transfers, supplier terms and vendor collaboration.

Every stock change in VE-JIT flows through `apply_stock_movement`, which
updates the cached `inventory_items.quantity` and appends an
`inventory_ledger` row. The ledger is the audit trail; the cached quantity is
a convenience for reads. Ledger rows are never edited or deleted.
"""

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.db.models import InventoryItem
from app.planes.models import InventoryLedgerEntry, WasteRecord

LEDGER_REASONS = (
    "SALE",
    "RECEIPT",
    "ADJUSTMENT",
    "TRANSFER_IN",
    "TRANSFER_OUT",
    "CYCLE_COUNT",
    "WASTE",
    "RETURN",
)


class OperationsError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class InsufficientStock(OperationsError):
    def __init__(self, sku: str, on_hand: int, requested: int):
        super().__init__(409, f"Insufficient stock for {sku}: on hand {on_hand}, requested {requested}")
        self.sku = sku
        self.on_hand = on_hand
        self.requested = requested


def next_ref(prefix: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"{prefix}-{stamp}-{uuid.uuid4().hex[:6].upper()}"


def get_or_create_item(db: Session, sku: str, *, name: Optional[str] = None) -> tuple[InventoryItem, bool]:
    item = db.query(InventoryItem).filter(InventoryItem.sku == sku).first()
    if item is not None:
        return item, False
    item = InventoryItem(sku=sku, name=name or sku, quantity=0, reorder_point=10, unit_cost=0.0)
    db.add(item)
    db.flush()
    return item, True


def apply_stock_movement(
    db: Session,
    *,
    sku: str,
    store_id: str,
    delta: int,
    reason: str,
    actor: str,
    ref: Optional[str] = None,
    name: Optional[str] = None,
) -> tuple[InventoryItem, bool]:
    """Apply a signed stock delta. Returns (item, created)."""
    if not actor or not actor.strip():
        raise OperationsError(422, "A non-empty actor is required for stock movements")
    if reason not in LEDGER_REASONS:
        raise OperationsError(422, f"Unknown ledger reason {reason!r}; expected one of {LEDGER_REASONS}")
    if delta == 0:
        raise OperationsError(422, "Stock movement delta must be non-zero")

    item, created = get_or_create_item(db, sku, name=name)
    new_quantity = item.quantity + int(delta)
    if new_quantity < 0:
        raise InsufficientStock(sku, item.quantity, -delta)

    item.quantity = new_quantity
    db.add(
        InventoryLedgerEntry(
            sku=sku,
            store_id=store_id,
            delta=int(delta),
            reason=reason,
            ref=ref,
            actor=actor.strip(),
        )
    )
    db.flush()
    return item, created


def record_waste(
    db: Session,
    *,
    sku: str,
    store_id: str,
    quantity: int,
    reason: str,
    recorded_by: str,
    unit: str = "unit",
    estimated_cost: float = 0.0,
) -> WasteRecord:
    if quantity <= 0:
        raise OperationsError(422, "Waste quantity must be positive")
    waste_reasons = {"SPOILAGE", "EXPIRED", "DAMAGE", "SHRINK", "OTHER"}
    if reason not in waste_reasons:
        raise OperationsError(422, f"Unknown waste reason {reason!r}; expected one of {sorted(waste_reasons)}")

    apply_stock_movement(
        db,
        sku=sku,
        store_id=store_id,
        delta=-quantity,
        reason="WASTE",
        actor=recorded_by,
        ref=f"waste:{reason}",
    )
    record = WasteRecord(
        sku=sku,
        store_id=store_id,
        quantity=quantity,
        unit=unit,
        reason=reason,
        estimated_cost=float(estimated_cost),
        recorded_by=recorded_by.strip(),
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def ledger_to_dict(entry: InventoryLedgerEntry) -> dict:
    return {
        "id": entry.id,
        "sku": entry.sku,
        "store_id": entry.store_id,
        "delta": entry.delta,
        "reason": entry.reason,
        "ref": entry.ref,
        "actor": entry.actor,
        "created_at": entry.created_at.isoformat() if entry.created_at else None,
    }
