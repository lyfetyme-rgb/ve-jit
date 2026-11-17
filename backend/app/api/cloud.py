"""Vendarix cloud API.

Two surfaces:

1. Policy signing — the only place bundles are signed. Disabled until
   CLOUD_SIGNER_TOKEN and POLICY_SIGNING_KEY are configured (no default key,
   ever). Signs with the active edge-signing key id.

2. Recovery ingestion — hash-chain continuity check, batch idempotency, and a
   quarantine-first conflict sink (HITL). See docs/EDGE_ARCHITECTURE.md and
   infrastructure/database/cloud_reconciliation.sql.
"""

import base64
import json
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.cloud import policy_signer, reconcile
from app.config import settings
from app.db.database import get_db
from app.pci_guard import forbid_cardholder_data_body

router = APIRouter(prefix="/api/v1/cloud", tags=["cloud"])


# -------------------------------------------------------------- schemas

class SignBundleRequest(BaseModel):
    payload: dict[str, Any]
    key_id: str = Field(..., min_length=1, max_length=128)
    policy_version: int = Field(..., ge=1)
    ttl_seconds: Optional[int] = Field(None, ge=60, le=31536000)


class EventIn(BaseModel):
    pass  # validated by the shared edge contract, not a pydantic subset


class IngestRecoveryRequest(BaseModel):
    recovery_session_id: str = Field(..., min_length=1, max_length=128)
    events: List[dict[str, Any]] = Field(..., min_length=1)
    verdicts: List[dict[str, Any]] = Field(default_factory=list)
    chain_tip: Optional[str] = None
    tenant_id: Optional[str] = None
    store_id: Optional[str] = None
    actor: str = Field(..., min_length=1)


class QuarantineResolveIn(BaseModel):
    resolution: str = Field(..., pattern="^(apply|discard|APPROVED_APPLY|REJECTED_VOID)$")
    actor: str = Field(..., min_length=1)
    note: Optional[str] = None


class CloudInventoryIn(BaseModel):
    sku: str = Field(..., min_length=1)
    quantity: int = Field(..., ge=0)
    tenant_id: Optional[str] = None
    store_id: Optional[str] = None
    actor: str = Field(..., min_length=1)


# --------------------------------------------------------- policy signing

@router.post("/policy/bundles/sign", status_code=201)
def sign_policy_bundle(
    payload: SignBundleRequest,
    x_cloud_signer_token: str = Header(default=""),
):
    if not settings.CLOUD_SIGNER_TOKEN or not settings.POLICY_SIGNING_KEY:
        raise HTTPException(status_code=503, detail="Cloud signer is disabled (set CLOUD_SIGNER_TOKEN and POLICY_SIGNING_KEY)")
    if x_cloud_signer_token != settings.CLOUD_SIGNER_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid cloud signer token")

    try:
        key = policy_signer.load_private_key_from_seed_b64(settings.POLICY_SIGNING_KEY)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=f"POLICY_SIGNING_KEY invalid: {exc}")

    ttl = payload.ttl_seconds or settings.POLICY_BUNDLE_TTL_SECONDS
    bundle = policy_signer.sign_policy_bundle(
        payload.payload,
        key_id=payload.key_id,
        private_key=key,
        policy_version=payload.policy_version,
        ttl_seconds=ttl,
    )
    return bundle


# --------------------------------------------------- recovery ingestion

@router.post("/recovery/sessions")
def ingest_recovery_session(payload: IngestRecoveryRequest, db: Session = Depends(get_db), _pci: None = Depends(forbid_cardholder_data_body("cloud.recovery_sessions"))):
    """Ingest a recovery batch from an edge node.

    Steps (one transaction): contract validation of every manifest, strict
    source_seq order, hash-chain continuity against the stored watermark tip,
    idempotent dedup on (tenant, store, event_id), then apply commutative
    deltas to cloud inventory with compare-and-swap; everything else lands in
    the HITL quarantine queue with a machine reason.
    """
    try:
        return reconcile.ingest_recovery_batch(
            db,
            tenant_id=payload.tenant_id or settings.TENANT_ID,
            store_id=payload.store_id or settings.STORE_ID,
            recovery_session_id=payload.recovery_session_id,
            events=payload.events,
            verdicts=payload.verdicts,
            actor=payload.actor,
            provided_chain_tip=payload.chain_tip,
        )
    except reconcile.ReconcileError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)


@router.get("/watermarks")
def list_watermarks(db: Session = Depends(get_db)):
    from app.cloud.models import CloudStoreWatermark

    rows = db.query(CloudStoreWatermark).order_by(CloudStoreWatermark.tenant_id.asc(), CloudStoreWatermark.store_id.asc()).all()
    return {"watermarks": [
        {
            "tenant_id": r.tenant_id,
            "store_id": r.store_id,
            "last_acked_seq": r.last_acked_seq,
            "last_verified_audit_hash": r.last_verified_audit_hash,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        }
        for r in rows
    ]}


@router.get("/quarantine")
def list_quarantine(status: str = "pending", limit: int = 50, db: Session = Depends(get_db)):
    from app.cloud.models import CloudReconciliationQuarantine

    query = db.query(CloudReconciliationQuarantine)
    if status == "pending":
        query = query.filter(CloudReconciliationQuarantine.status == "PENDING_REVIEW")
    elif status == "resolved":
        query = query.filter(CloudReconciliationQuarantine.status != "PENDING_REVIEW")
    rows = query.order_by(CloudReconciliationQuarantine.id.desc()).limit(min(limit, 500)).all()
    return {"quarantine": [reconcile.quarantine_to_dict(r) for r in rows]}


@router.post("/quarantine/{quarantine_id}/resolve")
def resolve_quarantine(quarantine_id: str, payload: QuarantineResolveIn, db: Session = Depends(get_db)):
    try:
        return reconcile.resolve_quarantine(
            db, quarantine_id=quarantine_id, resolution=payload.resolution, actor=payload.actor, note=payload.note,
        )
    except reconcile.ReconcileError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)


@router.post("/inventory/seed", status_code=201)
def seed_cloud_inventory(payload: CloudInventoryIn, db: Session = Depends(get_db)):
    """Seed the cloud-side inventory projection (initial load / onboarding)."""
    from app.cloud.models import CloudStoreInventory

    tenant_id = payload.tenant_id or settings.TENANT_ID
    store_id = payload.store_id or settings.STORE_ID
    existing = (
        db.query(CloudStoreInventory)
        .filter(CloudStoreInventory.tenant_id == tenant_id, CloudStoreInventory.store_id == store_id, CloudStoreInventory.sku == payload.sku)
        .first()
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail=f"Cloud inventory row for {payload.sku} already exists")
    row = CloudStoreInventory(tenant_id=tenant_id, store_id=store_id, sku=payload.sku, on_hand_qty=payload.quantity, version=1)
    db.add(row)
    db.commit()
    return {"sku": row.sku, "quantity": row.on_hand_qty, "tenant_id": tenant_id, "store_id": store_id, "seeded_by": payload.actor}


@router.get("/inventory")
def list_cloud_inventory(sku: Optional[str] = None, limit: int = 100, db: Session = Depends(get_db)):
    from app.cloud.models import CloudStoreInventory

    query = db.query(CloudStoreInventory)
    if sku:
        query = query.filter(CloudStoreInventory.sku == sku)
    rows = query.order_by(CloudStoreInventory.sku.asc()).limit(min(limit, 500)).all()
    return {"inventory": [
        {"tenant_id": r.tenant_id, "store_id": r.store_id, "sku": r.sku, "quantity": r.on_hand_qty, "version": r.version}
        for r in rows
    ]}
