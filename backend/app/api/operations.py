"""Operations plane API.

Operations owns the physical business processes: the inventory ledger,
purchasing and procurement, replenishment, receiving and put-away,
distribution-center transfers, supplier terms and SLAs, and vendor
collaboration.

Operations executes authorized business actions. Actions derived from an
Intelligence recommendation are rejected with 409 unless the recommendation
has been APPROVED. Manual actions require an explicit, non-empty actor.
Stock changes are posted to the append-only inventory ledger; the cached
`inventory_items.quantity` is a convenience view, never the audit trail.
"""

import json
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import settings
from app.db.database import get_db
from app.planes.governance import (
    RecommendationError,
    mark_executed,
    recommendation_to_dict,
    require_approvable_for_execution,
)
from app.planes.models import (
    InventoryLedgerEntry,
    PurchaseOrder,
    PurchaseOrderLine,
    Supplier,
    TransferOrder,
    VendorMessage,
    WasteRecord,
)
from app.planes.operations import (
    LEDGER_REASONS,
    OperationsError,
    apply_stock_movement,
    ledger_to_dict,
    next_ref,
    record_waste,
)
from app.edge.continuity import ContinuityError, require_action_allowed


def _safe_mode_guard(db: Session, action_class: str) -> None:
    """Hard safety gate: SAFE_MODE blocks unsafe action classes regardless of score."""
    try:
        require_action_allowed(db, action_class)
    except ContinuityError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)

router = APIRouter(prefix="/api/v1/operations", tags=["operations"])


# ------------------------------------------------------------------ schemas

class Adjustment(BaseModel):
    sku: str = Field(..., min_length=1)
    quantity_delta: int
    reason: str = Field("ADJUSTMENT", pattern="^(ADJUSTMENT|CYCLE_COUNT)$")
    actor: str = Field(..., min_length=1)
    note: Optional[str] = None


class WasteIn(BaseModel):
    sku: str = Field(..., min_length=1)
    quantity: int = Field(..., ge=1)
    reason: str = Field(..., pattern="^(SPOILAGE|EXPIRED|DAMAGE|SHRINK|OTHER)$")
    estimated_cost: float = Field(0.0, ge=0)
    unit: str = "unit"
    actor: str = Field(..., min_length=1)


class SupplierCreate(BaseModel):
    name: str = Field(..., min_length=1)
    contact_email: Optional[str] = None
    payment_terms: str = "NET30"
    lead_time_days: int = Field(7, ge=0)
    sla_otif_pct: float = Field(95.0, ge=0, le=100)
    sla_fill_rate_pct: float = Field(98.0, ge=0, le=100)


class POLineIn(BaseModel):
    sku: str = Field(..., min_length=1)
    quantity_ordered: int = Field(..., ge=1)
    unit_cost: float = Field(0.0, ge=0)


class PurchaseOrderCreate(BaseModel):
    supplier_id: int
    lines: List[POLineIn] = Field(..., min_length=1)
    store_id: Optional[str] = None
    expected_at: Optional[datetime] = None
    notes: Optional[str] = None
    created_by: str = Field(..., min_length=1)


class ApprovePurchaseOrder(BaseModel):
    approver: str = Field(..., min_length=1)
    note: Optional[str] = None


class CancelPurchaseOrder(BaseModel):
    actor: str = Field(..., min_length=1)
    reason: str = Field(..., min_length=1)


class ReceivingLine(BaseModel):
    sku: str = Field(..., min_length=1)
    quantity_received: int = Field(..., ge=1)


class ReceivingIn(BaseModel):
    po_number: str = Field(..., min_length=1)
    actor: str = Field(..., min_length=1)
    lines: List[ReceivingLine] = Field(..., min_length=1)


class TransferCreate(BaseModel):
    from_store: str = Field(..., min_length=1)
    to_store: str = Field(..., min_length=1)
    sku: str = Field(..., min_length=1)
    quantity: int = Field(..., ge=1)
    created_by: str = Field(..., min_length=1)


class TransferAction(BaseModel):
    actor: str = Field(..., min_length=1)


class VendorMessageIn(BaseModel):
    direction: str = Field(..., pattern="^(TO_VENDOR|FROM_VENDOR)$")
    subject: str = Field(..., min_length=1, max_length=255)
    body: str = Field(..., min_length=1)
    author: str = Field(..., min_length=1)
    po_number: Optional[str] = None


class ExecuteRecommendation(BaseModel):
    actor: str = Field(..., min_length=1)
    note: Optional[str] = None


# ------------------------------------------------------------- inventory

@router.get("/ledger")
def list_ledger(
    sku: Optional[str] = None,
    reason: Optional[str] = None,
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    query = db.query(InventoryLedgerEntry)
    if sku:
        query = query.filter(InventoryLedgerEntry.sku == sku)
    if reason:
        query = query.filter(InventoryLedgerEntry.reason == reason)
    rows = query.order_by(InventoryLedgerEntry.id.desc()).limit(limit).all()
    return {"entries": [ledger_to_dict(e) for e in rows], "reasons": list(LEDGER_REASONS)}


@router.post("/adjustments", status_code=201)
def adjust_stock(payload: Adjustment, db: Session = Depends(get_db)):
    _safe_mode_guard(db, "inventory_move")
    try:
        item, created = apply_stock_movement(
            db,
            sku=payload.sku,
            store_id=settings.STORE_ID,
            delta=payload.quantity_delta,
            reason=payload.reason,
            actor=payload.actor,
            ref=payload.note,
        )
        db.commit()
    except OperationsError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    return {"sku": item.sku, "quantity": item.quantity, "item_created": created, "reason": payload.reason}


@router.post("/waste", status_code=201)
def record_waste_event(payload: WasteIn, db: Session = Depends(get_db)):
    _safe_mode_guard(db, "waste_record")
    try:
        record = record_waste(
            db,
            sku=payload.sku,
            store_id=settings.STORE_ID,
            quantity=payload.quantity,
            reason=payload.reason,
            recorded_by=payload.actor,
            unit=payload.unit,
            estimated_cost=payload.estimated_cost,
        )
    except OperationsError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    return _waste_out(record)


# ------------------------------------------------------------- suppliers

@router.post("/suppliers", status_code=201)
def create_supplier(payload: SupplierCreate, db: Session = Depends(get_db)):
    supplier = Supplier(**payload.model_dump(), active=True)
    db.add(supplier)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=409, detail=f"Supplier {payload.name!r} already exists")
    db.refresh(supplier)
    return _supplier_out(supplier)


@router.get("/suppliers")
def list_suppliers(active_only: bool = True, db: Session = Depends(get_db)):
    query = db.query(Supplier)
    if active_only:
        query = query.filter(Supplier.active.is_(True))
    return {"suppliers": [_supplier_out(s) for s in query.order_by(Supplier.name.asc()).all()]}


@router.get("/suppliers/{supplier_id}")
def get_supplier(supplier_id: int, db: Session = Depends(get_db)):
    supplier = db.query(Supplier).filter(Supplier.id == supplier_id).first()
    if supplier is None:
        raise HTTPException(status_code=404, detail="Supplier not found")
    return _supplier_out(supplier)


# ------------------------------------------------------ purchase orders

@router.post("/purchase-orders", status_code=201)
def create_purchase_order(payload: PurchaseOrderCreate, db: Session = Depends(get_db)):
    supplier = db.query(Supplier).filter(Supplier.id == payload.supplier_id).first()
    if supplier is None:
        raise HTTPException(status_code=404, detail=f"Supplier {payload.supplier_id} not found")

    po = PurchaseOrder(
        po_number=next_ref("PO"),
        supplier_id=supplier.id,
        store_id=payload.store_id or settings.STORE_ID,
        status="DRAFT",
        created_by=payload.created_by,
        expected_at=payload.expected_at,
        notes=payload.notes,
    )
    db.add(po)
    db.flush()
    for line in payload.lines:
        db.add(
            PurchaseOrderLine(
                po_id=po.id,
                sku=line.sku,
                quantity_ordered=line.quantity_ordered,
                unit_cost=line.unit_cost,
            )
        )
    db.commit()
    db.refresh(po)
    return _po_detail(db, po)


@router.get("/purchase-orders")
def list_purchase_orders(status: Optional[str] = None, limit: int = Query(50, ge=1, le=500), db: Session = Depends(get_db)):
    query = db.query(PurchaseOrder)
    if status:
        query = query.filter(PurchaseOrder.status == status)
    pos = query.order_by(PurchaseOrder.id.desc()).limit(limit).all()
    return {"purchase_orders": [_po_detail(db, po, lines=False) for po in pos]}


@router.get("/purchase-orders/{po_number}")
def get_purchase_order(po_number: str, db: Session = Depends(get_db)):
    return _po_detail(db, _find_po(db, po_number))


@router.post("/purchase-orders/{po_number}/approve")
def approve_purchase_order(po_number: str, payload: ApprovePurchaseOrder, db: Session = Depends(get_db)):
    _safe_mode_guard(db, "purchase_order")
    po = _find_po(db, po_number)
    if po.status != "DRAFT":
        raise HTTPException(status_code=409, detail=f"Only DRAFT purchase orders can be approved; {po_number} is {po.status}")
    if payload.approver.strip() == po.created_by.strip():
        raise HTTPException(status_code=403, detail="Segregation of duties: the approver must not be the creator")
    po.status = "APPROVED"
    po.approved_by = payload.approver.strip()
    po.approved_at = datetime.now(timezone.utc)
    if payload.note:
        po.notes = (po.notes + "\n" if po.notes else "") + f"approval: {payload.note}"
    db.commit()
    db.refresh(po)
    return _po_detail(db, po)


@router.post("/purchase-orders/{po_number}/cancel")
def cancel_purchase_order(po_number: str, payload: CancelPurchaseOrder, db: Session = Depends(get_db)):
    po = _find_po(db, po_number)
    if po.status in ("RECEIVED", "CANCELLED"):
        raise HTTPException(status_code=409, detail=f"{po_number} is {po.status} and cannot be cancelled")
    po.status = "CANCELLED"
    po.notes = (po.notes + "\n" if po.notes else "") + f"cancelled by {payload.actor}: {payload.reason}"
    db.commit()
    return _po_detail(db, po)


# -------------------------------------------------------------- receiving

@router.post("/receiving", status_code=201)
def receive_against_po(payload: ReceivingIn, db: Session = Depends(get_db)):
    _safe_mode_guard(db, "receiving_put_away")
    po = _find_po(db, payload.po_number)
    if po.status == "CANCELLED":
        raise HTTPException(status_code=409, detail=f"{po.po_number} is cancelled")
    if po.status == "DRAFT":
        raise HTTPException(status_code=409, detail=f"{po.po_number} is still DRAFT; approve it before receiving")

    lines = {line.sku: line for line in db.query(PurchaseOrderLine).filter(PurchaseOrderLine.po_id == po.id).all()}
    for receipt in payload.lines:
        if receipt.sku not in lines:
            raise HTTPException(status_code=422, detail=f"SKU {receipt.sku} is not on PO {po.po_number}")

    created_skus = []
    for receipt in payload.lines:
        line = lines[receipt.sku]
        outstanding = line.quantity_ordered - line.quantity_received
        if receipt.quantity_received > outstanding:
            raise HTTPException(
                status_code=422,
                detail=f"Receiving {receipt.quantity_received} of {receipt.sku} exceeds outstanding {outstanding}",
            )
        try:
            _, created = apply_stock_movement(
                db,
                sku=receipt.sku,
                store_id=po.store_id,
                delta=receipt.quantity_received,
                reason="RECEIPT",
                actor=payload.actor,
                ref=po.po_number,
            )
        except OperationsError as exc:
            db.rollback()
            raise HTTPException(status_code=exc.status_code, detail=exc.detail)
        if created:
            created_skus.append(receipt.sku)
        line.quantity_received += receipt.quantity_received

    all_lines = list(lines.values())
    fully_received = all(l.quantity_received >= l.quantity_ordered for l in all_lines)
    po.status = "RECEIVED" if fully_received else "PARTIALLY_RECEIVED"
    if fully_received:
        po.received_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(po)
    result = _po_detail(db, po)
    result["created_skus"] = created_skus
    return result


# ------------------------------------------------------ DC transfers

@router.post("/transfers", status_code=201)
def create_transfer(payload: TransferCreate, db: Session = Depends(get_db)):
    if payload.from_store == payload.to_store:
        raise HTTPException(status_code=422, detail="from_store and to_store must differ")
    transfer = TransferOrder(
        transfer_ref=next_ref("TRF"),
        from_store=payload.from_store,
        to_store=payload.to_store,
        sku=payload.sku,
        quantity=payload.quantity,
        status="REQUESTED",
        created_by=payload.created_by,
    )
    db.add(transfer)
    db.commit()
    db.refresh(transfer)
    return _transfer_out(transfer)


@router.get("/transfers")
def list_transfers(status: Optional[str] = None, limit: int = Query(50, ge=1, le=500), db: Session = Depends(get_db)):
    query = db.query(TransferOrder)
    if status:
        query = query.filter(TransferOrder.status == status)
    rows = query.order_by(TransferOrder.id.desc()).limit(limit).all()
    return {"transfers": [_transfer_out(t) for t in rows]}


@router.get("/transfers/{transfer_ref}")
def get_transfer(transfer_ref: str, db: Session = Depends(get_db)):
    return _transfer_out(_find_transfer(db, transfer_ref))


@router.post("/transfers/{transfer_ref}/ship")
def ship_transfer(transfer_ref: str, payload: TransferAction, db: Session = Depends(get_db)):
    _safe_mode_guard(db, "transfer_ship")
    transfer = _find_transfer(db, transfer_ref)
    if transfer.status != "REQUESTED":
        raise HTTPException(status_code=409, detail=f"{transfer_ref} is {transfer.status}; only REQUESTED transfers ship")
    try:
        apply_stock_movement(
            db,
            sku=transfer.sku,
            store_id=transfer.from_store,
            delta=-transfer.quantity,
            reason="TRANSFER_OUT",
            actor=payload.actor,
            ref=transfer.transfer_ref,
        )
    except OperationsError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    transfer.status = "SHIPPED"
    transfer.shipped_by = payload.actor
    transfer.shipped_at = datetime.now(timezone.utc)
    db.commit()
    return _transfer_out(transfer)


@router.post("/transfers/{transfer_ref}/receive")
def receive_transfer(transfer_ref: str, payload: TransferAction, db: Session = Depends(get_db)):
    _safe_mode_guard(db, "transfer_receive")
    transfer = _find_transfer(db, transfer_ref)
    if transfer.status != "SHIPPED":
        raise HTTPException(status_code=409, detail=f"{transfer_ref} is {transfer.status}; only SHIPPED transfers can be received")
    try:
        apply_stock_movement(
            db,
            sku=transfer.sku,
            store_id=transfer.to_store,
            delta=transfer.quantity,
            reason="TRANSFER_IN",
            actor=payload.actor,
            ref=transfer.transfer_ref,
        )
    except OperationsError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    transfer.status = "RECEIVED"
    transfer.received_by = payload.actor
    transfer.received_at = datetime.now(timezone.utc)
    db.commit()
    return _transfer_out(transfer)


# ----------------------------------------------------- vendor collaboration

@router.post("/vendors/{supplier_id}/messages", status_code=201)
def post_vendor_message(supplier_id: int, payload: VendorMessageIn, db: Session = Depends(get_db)):
    supplier = db.query(Supplier).filter(Supplier.id == supplier_id).first()
    if supplier is None:
        raise HTTPException(status_code=404, detail=f"Supplier {supplier_id} not found")
    if payload.po_number:
        _find_po(db, payload.po_number)
    message = VendorMessage(
        supplier_id=supplier.id,
        po_number=payload.po_number,
        direction=payload.direction,
        subject=payload.subject,
        body=payload.body,
        author=payload.author,
    )
    db.add(message)
    db.commit()
    db.refresh(message)
    return _message_out(message)


@router.get("/vendors/{supplier_id}/messages")
def list_vendor_messages(supplier_id: int, db: Session = Depends(get_db)):
    supplier = db.query(Supplier).filter(Supplier.id == supplier_id).first()
    if supplier is None:
        raise HTTPException(status_code=404, detail=f"Supplier {supplier_id} not found")
    rows = (
        db.query(VendorMessage)
        .filter(VendorMessage.supplier_id == supplier.id)
        .order_by(VendorMessage.id.desc())
        .all()
    )
    return {"supplier_id": supplier.id, "messages": [_message_out(m) for m in rows]}


# ----------------------------------- execute approved intelligence actions

@router.post("/recommendations/{uid}/execute")
def execute_recommendation(uid: str, payload: ExecuteRecommendation, db: Session = Depends(get_db)):
    """Execute an APPROVED Intelligence recommendation.

    This is the only bridge from Intelligence to action. PROPOSED or REJECTED
    recommendations return 409. Supported kinds:

    - REPLENISHMENT -> creates a purchase order (status APPROVED) from the
      recommendation payload: {supplier_id, lines: [{sku, quantity_ordered, unit_cost?}]}
    - TRANSFER -> creates a REQUESTED transfer: {from_store, to_store, sku, quantity}
    - anything else -> acknowledged with no side effects (marks EXECUTED)
    """
    try:
        rec = require_approvable_for_execution(db, uid)
    except RecommendationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)

    kind_guard = {
        "REPLENISHMENT": "purchase_order",
        "TRANSFER": "transfer_ship",
        "RECONCILIATION": "inventory_move",
        "WASTE_REVIEW": "waste_record",
    }.get(rec.kind)
    if kind_guard:
        _safe_mode_guard(db, kind_guard)

    action = _parse_action(rec.recommended_action)
    execution_ref: Optional[str] = None
    detail: dict = {}

    if rec.kind == "REPLENISHMENT":
        if not action:
            raise HTTPException(status_code=422, detail="REPLENISHMENT recommendation carries no recommended_action payload")
        supplier_id = action.get("supplier_id")
        lines = action.get("lines")
        if supplier_id is None or not lines:
            # tolerate a minimal payload: {sku, suggested_order_quantity} from the anomaly scanner
            sku = action.get("sku")
            qty = action.get("suggested_order_quantity")
            if sku and qty:
                supplier = db.query(Supplier).filter(Supplier.active.is_(True)).order_by(Supplier.id.asc()).first()
                if supplier is None:
                    raise HTTPException(status_code=422, detail="No active supplier available to route the replenishment PO to")
                supplier_id = supplier.id
                lines = [{"sku": sku, "quantity_ordered": int(qty), "unit_cost": 0.0}]
                detail["routed_to_supplier_id"] = supplier.id
            else:
                raise HTTPException(
                    status_code=422,
                    detail="REPLENISHMENT payload must include supplier_id and lines (or sku + suggested_order_quantity)",
                )
        supplier = db.query(Supplier).filter(Supplier.id == int(supplier_id)).first()
        if supplier is None:
            raise HTTPException(status_code=404, detail=f"Supplier {supplier_id} not found")
        po = PurchaseOrder(
            po_number=next_ref("PO"),
            supplier_id=supplier.id,
            store_id=settings.STORE_ID,
            status="APPROVED",
            created_by=f"recommendation:{rec.uid}",
            approved_by=rec.approved_by,
            approved_at=datetime.now(timezone.utc),
            notes=f"auto-created from approved recommendation {rec.uid}",
        )
        db.add(po)
        db.flush()
        for line in lines:
            db.add(
                PurchaseOrderLine(
                    po_id=po.id,
                    sku=line["sku"],
                    quantity_ordered=int(line["quantity_ordered"]),
                    unit_cost=float(line.get("unit_cost", 0.0)),
                )
            )
        execution_ref = po.po_number
        detail["purchase_order"] = po.po_number

    elif rec.kind == "TRANSFER":
        if not action:
            raise HTTPException(status_code=422, detail="TRANSFER recommendation carries no recommended_action payload")
        try:
            transfer = TransferOrder(
                transfer_ref=next_ref("TRF"),
                from_store=action["from_store"],
                to_store=action["to_store"],
                sku=action["sku"],
                quantity=int(action["quantity"]),
                status="REQUESTED",
                created_by=f"recommendation:{rec.uid}",
            )
        except KeyError as missing:
            raise HTTPException(status_code=422, detail=f"TRANSFER payload missing key {missing}")
        db.add(transfer)
        execution_ref = transfer.transfer_ref
        detail["transfer"] = transfer.transfer_ref

    elif rec.kind == "RECONCILIATION":
        if not action or not action.get("sku") or action.get("counted_quantity") is None:
            raise HTTPException(status_code=422, detail="RECONCILIATION payload must include sku and counted_quantity")
        from app.db.models import InventoryItem

        sku = action["sku"]
        counted = int(action["counted_quantity"])
        item = db.query(InventoryItem).filter(InventoryItem.sku == sku).first()
        expected = item.quantity if item else 0
        delta = counted - expected
        if delta == 0:
            execution_ref = f"reconciliation:{rec.uid}:in_sync"
            detail = {"sku": sku, "counted_quantity": counted, "expected_quantity": expected, "variance": 0, "cycle_count_applied": False}
        else:
            apply_stock_movement(
                db,
                sku=sku,
                store_id=settings.STORE_ID,
                delta=delta,
                reason="CYCLE_COUNT",
                actor=payload.actor,
                ref=rec.uid,
            )
            execution_ref = f"cycle-count:{rec.uid}"
            detail = {"sku": sku, "counted_quantity": counted, "expected_quantity": expected, "variance": delta, "cycle_count_applied": True}

    else:
        execution_ref = f"ack:{rec.uid}"
        detail["acknowledged"] = True

    mark_executed(db, rec, actor=payload.actor, execution_ref=execution_ref, note=payload.note)
    return {
        "recommendation": recommendation_to_dict(rec),
        "execution_ref": execution_ref,
        "detail": detail,
    }


@router.get("/actions")
def list_authorized_actions(limit: int = Query(50, ge=1, le=500), db: Session = Depends(get_db)):
    """Audit view of recommendation-driven actions (the authorized-action ledger)."""
    from app.planes.models import Recommendation

    rows = (
        db.query(Recommendation)
        .filter(Recommendation.status == "EXECUTED")
        .order_by(Recommendation.id.desc())
        .limit(limit)
        .all()
    )
    return {"actions": [recommendation_to_dict(r) for r in rows]}


# ------------------------------------------------------------------ helpers

def _parse_action(raw: Optional[str]) -> Optional[dict]:
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except (ValueError, TypeError):
        return None


def _find_po(db: Session, po_number: str) -> PurchaseOrder:
    po = db.query(PurchaseOrder).filter(PurchaseOrder.po_number == po_number).first()
    if po is None:
        raise HTTPException(status_code=404, detail=f"Purchase order {po_number} not found")
    return po


def _find_transfer(db: Session, transfer_ref: str) -> TransferOrder:
    transfer = db.query(TransferOrder).filter(TransferOrder.transfer_ref == transfer_ref).first()
    if transfer is None:
        raise HTTPException(status_code=404, detail=f"Transfer {transfer_ref} not found")
    return transfer


def _supplier_out(s: Supplier) -> dict:
    return {
        "id": s.id,
        "name": s.name,
        "contact_email": s.contact_email,
        "payment_terms": s.payment_terms,
        "lead_time_days": s.lead_time_days,
        "sla_otif_pct": s.sla_otif_pct,
        "sla_fill_rate_pct": s.sla_fill_rate_pct,
        "active": s.active,
    }


def _waste_out(w: WasteRecord) -> dict:
    return {
        "id": w.id,
        "sku": w.sku,
        "store_id": w.store_id,
        "quantity": w.quantity,
        "unit": w.unit,
        "reason": w.reason,
        "estimated_cost": w.estimated_cost,
        "recorded_by": w.recorded_by,
        "created_at": w.created_at.isoformat() if w.created_at else None,
    }


def _po_detail(db: Session, po: PurchaseOrder, lines: bool = True) -> dict:
    detail = {
        "po_number": po.po_number,
        "supplier_id": po.supplier_id,
        "store_id": po.store_id,
        "status": po.status,
        "created_by": po.created_by,
        "approved_by": po.approved_by,
        "expected_at": po.expected_at.isoformat() if po.expected_at else None,
        "received_at": po.received_at.isoformat() if po.received_at else None,
        "approved_at": po.approved_at.isoformat() if po.approved_at else None,
        "notes": po.notes,
        "created_at": po.created_at.isoformat() if po.created_at else None,
    }
    if lines:
        detail["lines"] = [
            {
                "sku": line.sku,
                "quantity_ordered": line.quantity_ordered,
                "quantity_received": line.quantity_received,
                "unit_cost": line.unit_cost,
            }
            for line in db.query(PurchaseOrderLine).filter(PurchaseOrderLine.po_id == po.id).all()
        ]
    return detail


def _transfer_out(t: TransferOrder) -> dict:
    return {
        "transfer_ref": t.transfer_ref,
        "from_store": t.from_store,
        "to_store": t.to_store,
        "sku": t.sku,
        "quantity": t.quantity,
        "status": t.status,
        "created_by": t.created_by,
        "shipped_by": t.shipped_by,
        "received_by": t.received_by,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "shipped_at": t.shipped_at.isoformat() if t.shipped_at else None,
        "received_at": t.received_at.isoformat() if t.received_at else None,
    }


def _message_out(m: VendorMessage) -> dict:
    return {
        "id": m.id,
        "supplier_id": m.supplier_id,
        "po_number": m.po_number,
        "direction": m.direction,
        "subject": m.subject,
        "body": m.body,
        "author": m.author,
        "created_at": m.created_at.isoformat() if m.created_at else None,
    }
