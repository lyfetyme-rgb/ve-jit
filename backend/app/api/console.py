"""Store Console API — multi-tenant retail operations on the governed core.

Every route is tenant/store scoped. Mutations use the same governance path as
the rest of VE-JIT: recommendations are proposed by Intelligence and only
executed by Operations after an explicit human approval.
"""

import json
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.console.models import ConsoleStore, ConsoleTenant
from app.console.seed import get_sku_prefix
from app.db.database import get_db
from app.db.models import InventoryItem
from app.edge.contracts import SCHEMA_VERSION
from app.pci_guard import forbid_cardholder_data_body
from app.planes.commerce import CommerceError, place_order
from app.planes.governance import (
    RecommendationError,
    approve_recommendation,
    get_recommendation,
)
from app.planes.models import CommerceOrder, ProductPrice, Recommendation, InventoryLedgerEntry
from app.security.principal import (
    Principal,
    require_actor,
    require_permission,
    require_store,
    require_tenant,
)

router = APIRouter(prefix="/api/v1/console", tags=["console"])


class ActorIn(BaseModel):
    actor: str = Field(..., min_length=1)
    note: Optional[str] = None
    tenant_id: Optional[str] = None


class SaleLine(BaseModel):
    sku: str
    quantity: int = Field(1, ge=1)


class SaleIn(BaseModel):
    store_id: str
    tenant_id: str
    channel: str = "pos"
    lines: list[SaleLine] = Field(..., min_length=1)
    client_order_id: Optional[str] = None
    member_number: Optional[str] = None
    payment_token: Optional[str] = None  # opaque PSP token, never a PAN


def _store(db: Session, tenant_id: str, store_id: str) -> ConsoleStore:
    row = (
        db.query(ConsoleStore)
        .filter(ConsoleStore.tenant_id == tenant_id, ConsoleStore.store_id == store_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail=f"Store {store_id!r} is not part of tenant {tenant_id!r}")
    return row


@router.get("/catalog")
def catalog(
    db: Session = Depends(get_db),
    _principal: Principal = Depends(require_permission("read:console")),
):
    tenants = (
        [{"tenant_id": t.tenant_id, "name": t.name, "vertical": t.vertical, "description": t.description}
         for t in db.query(ConsoleTenant).order_by(ConsoleTenant.id.asc()).all()]
    )
    stores = (
        [{"store_id": s.store_id, "tenant_id": s.tenant_id, "name": s.name,
          "location": s.location, "architecture": s.architecture, "channel": s.channel}
         for s in db.query(ConsoleStore).order_by(ConsoleStore.id.asc()).all()]
    )
    return {"tenants": tenants, "stores": stores, "schema_version": SCHEMA_VERSION}


@router.get("/inventory")
def inventory(
    tenant_id: str,
    store_id: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_permission("read:console")),
):
    require_store(principal, tenant_id, store_id)
    _store(db, tenant_id, store_id)
    prefix = get_sku_prefix(db, tenant_id)
    items = db.query(InventoryItem).filter(InventoryItem.sku.like(prefix + "%")).order_by(InventoryItem.sku.asc()).all()
    return {
        "store_id": store_id,
        "items": [
            {
                "sku": i.sku, "name": i.name, "on_hand": i.quantity,
                "reorder_point": i.reorder_point, "unit_cost": i.unit_cost,
                "status": "OUT" if i.quantity <= 0 else "LOW" if i.quantity <= i.reorder_point else "OK",
            }
            for i in items
        ],
    }


@router.get("/overview")
def overview(
    tenant_id: str,
    store_id: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_permission("read:console")),
):
    require_store(principal, tenant_id, store_id)
    _store(db, tenant_id, store_id)
    prefix = get_sku_prefix(db, tenant_id)
    items = db.query(InventoryItem).filter(InventoryItem.sku.like(prefix + "%")).all()
    recs = db.query(Recommendation).filter(Recommendation.tenant_id == tenant_id, Recommendation.store_id == store_id).all()
    orders = db.query(CommerceOrder).filter(CommerceOrder.store_id == store_id).all()
    ledger_entries = db.query(InventoryLedgerEntry).filter(InventoryLedgerEntry.store_id == store_id).count()
    return {
        "tenant_id": tenant_id,
        "store_id": store_id,
        "skus": len(items),
        "on_hand_units": sum(i.quantity for i in items),
        "low_stock_skus": sum(1 for i in items if i.quantity <= i.reorder_point),
        "open_recommendations": sum(1 for r in recs if r.status in ("PROPOSED", "APPROVED")),
        "orders": len(orders),
        "sales_total": round(sum(o.total for o in orders), 2),
        "ledger_entries": ledger_entries,
        "governance_rule": (
            "Intelligence produces recommendations. Operations executes authorized "
            "business actions. Commerce remains responsible for the customer-facing transaction."
        ),
    }


@router.get("/recommendations")
def recommendations(
    tenant_id: str,
    store_id: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_permission("read:console")),
):
    require_store(principal, tenant_id, store_id)
    _store(db, tenant_id, store_id)
    rows = (
        db.query(Recommendation)
        .filter(Recommendation.tenant_id == tenant_id, Recommendation.store_id == store_id)
        .order_by(Recommendation.id.desc())
        .all()
    )
    return {"recommendations": [
        {
            "uid": r.uid, "kind": r.kind, "status": r.status, "summary": r.summary,
            "confidence": r.confidence, "approved_by": r.approved_by,
            "executed_by": r.executed_by, "execution_ref": r.execution_ref,
            "recommended_action": json.loads(r.recommended_action) if r.recommended_action else None,
        }
        for r in rows
    ]}


@router.post("/recommendations/{uid}/approve")
def approve(
    uid: str,
    payload: ActorIn,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_permission("recommendation:approve")),
):
    try:
        rec = get_recommendation(db, uid)
    except RecommendationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    require_tenant(principal, rec.tenant_id or "")
    require_actor(principal, payload.actor)
    try:
        updated = approve_recommendation(db, uid, approver=principal.subject, note=payload.note)
    except RecommendationError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    return {"uid": updated.uid, "status": updated.status, "approved_by": updated.approved_by}


@router.post("/recommendations/{uid}/execute")
def execute(
    uid: str,
    payload: ActorIn,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_permission("recommendation:execute")),
):
    try:
        rec = get_recommendation(db, uid)
    except RecommendationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    require_tenant(principal, rec.tenant_id or "")
    require_actor(principal, payload.actor)
    # Reuse the governed Operations executor — no console-specific execution path.
    from app.api.operations import ExecuteRecommendation, execute_recommendation

    return execute_recommendation(
        uid, ExecuteRecommendation(actor=principal.subject, note=payload.note), db
    )


@router.post("/orders", status_code=201)
def sale(
    payload: SaleIn,
    db: Session = Depends(get_db),
    _pci: None = Depends(forbid_cardholder_data_body("console.sale")),
    principal: Principal = Depends(require_permission("sale:create")),
):
    require_store(principal, payload.tenant_id, payload.store_id)
    _store(db, payload.tenant_id, payload.store_id)

    # Authorize through the configured boundary before recording the sale.
    # The PCI guard has already rejected any cardholder data in the body.
    from app.integrations.payments import PaymentError, current_adapter

    amount = 0.0
    for line in payload.lines:
        price = (
            db.query(ProductPrice)
            .filter(ProductPrice.sku == line.sku, ProductPrice.store_id == payload.store_id)
            .first()
        )
        if price is not None:
            amount += float(price.unit_price) * line.quantity

    try:
        authorization = current_adapter().authorize(
            reference=payload.payment_token or payload.client_order_id or f"pos-{uuid.uuid4().hex}",
            amount=round(amount, 2),
            store_id=payload.store_id,
        )
    except PaymentError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    try:
        order, created = place_order(
            db,
            channel=payload.channel,
            store_id=payload.store_id,
            register_id="console",
            payment_method="card",
            member_number=payload.member_number,
            client_order_id=payload.client_order_id,
            lines=[line.model_dump() for line in payload.lines],
        )
    except CommerceError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    return {
        "order_ref": order.order_ref,
        "status": order.status,
        "total": order.total,
        "authorization": authorization,
        "created": created,
    }


@router.get("/audit")
def audit(
    tenant_id: str,
    store_id: str,
    limit: int = 50,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_permission("read:console")),
):
    require_store(principal, tenant_id, store_id)
    _store(db, tenant_id, store_id)
    entries = (
        db.query(InventoryLedgerEntry)
        .filter(InventoryLedgerEntry.store_id == store_id)
        .order_by(InventoryLedgerEntry.id.desc())
        .limit(limit)
        .all()
    )
    recs = (
        db.query(Recommendation)
        .filter(Recommendation.tenant_id == tenant_id, Recommendation.store_id == store_id,
                Recommendation.status == "EXECUTED")
        .order_by(Recommendation.id.desc())
        .limit(limit)
        .all()
    )
    return {
        "ledger": [
            {"sku": e.sku, "delta": e.delta, "reason": e.reason, "ref": e.ref, "actor": e.actor}
            for e in entries
        ],
        "authorized_actions": [
            {"uid": r.uid, "kind": r.kind, "approved_by": r.approved_by,
             "executed_by": r.executed_by, "execution_ref": r.execution_ref}
            for r in recs
        ],
    }
