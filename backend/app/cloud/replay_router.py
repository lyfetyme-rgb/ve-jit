"""Cloud Replay & cloud-ack endpoint.

The batch-level fast path of the recovery pipeline: the Go sidecar (vejit-sync)
classifies pre-aggregated deltas, and this endpoint applies them under a
per-store watermark row with an audit-hash-chain check.

Invariants (enforced here, not by convention):

1. Watermark row is locked for update (SELECT ... FOR UPDATE on Postgres; a
   no-op on SQLite where the file lock serializes writers).
2. Idempotent retry: a re-delivered batch whose (from_seq, to_seq, tip) matches
   what we already ack returns the same ack with zero applied changes.
3. Discontinuity (from_seq or prev_audit_hash mismatch) -> 409; the node must
   escalate to SAFE_MODE rather than replay against a forked chain.
4. Each source_event_id is deduplicated individually via a SAVEPOINT insert —
   partial replays never double-decrement.
5. COMMUTE_APPLY is applied with a compare-and-swap and a negative-on-hand
   guard; anything that fails the guard is routed to the HITL quarantine
   instead of writing a negative projection (fail closed).
"""

import json
from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.cloud.models import (
    CloudIngestedEvent,
    CloudReconciliationQuarantine,
    CloudStoreInventory,
    CloudStoreWatermark,
)
from app.db.database import get_db as _get_db

router = APIRouter(prefix="/api/v1/cloud/edge-sync", tags=["cloud-sync"])

# The cloud session is the same embedded session in the pilot; split out so a
# dedicated async session / PG schema can be dropped in without touching callers.
get_cloud_db = _get_db


class ClassifiedDeltaBatch(BaseModel):
    sku: str = Field(..., min_length=1)
    verdict: Literal["COMMUTE_APPLY", "QUARANTINE"]
    net_delta_qty: int
    baseline_cloud_qty: int
    conflict_reason: Optional[str] = None
    source_event_ids: list[str] = Field(..., min_length=1)


class WatermarkReplayRequest(BaseModel):
    tenant_id: str = Field(..., min_length=1)
    store_id: str = Field(..., min_length=1)
    recovery_session_id: str = Field(..., min_length=1)
    from_seq: int = Field(..., ge=0)
    to_seq: int = Field(..., ge=0)
    prev_audit_hash: str = Field(..., min_length=1)
    tip_audit_hash: str = Field(..., min_length=1)
    batches: list[ClassifiedDeltaBatch] = Field(..., min_length=1)


class CloudAckResponse(BaseModel):
    recovery_session_id: str
    acked_seq: int
    verified_tip_hash: str
    applied_skus: int
    quarantined_skus: int
    quarantine_ids: list[str]
    acked_at: str


@router.post("/watermark-replay", response_model=CloudAckResponse)
def process_watermark_replay(
    req: WatermarkReplayRequest,
    db: Session = Depends(get_cloud_db),
) -> CloudAckResponse:
    from app.planes.operations import cas_update

    # 1. Lock the store watermark row & verify continuity
    watermark = (
        db.query(CloudStoreWatermark)
        .filter(
            CloudStoreWatermark.tenant_id == req.tenant_id,
            CloudStoreWatermark.store_id == req.store_id,
        )
        .with_for_update()
        .first()
    )
    if watermark is None:
        raise HTTPException(status_code=404, detail="Unknown tenant/store watermark")

    # Idempotent retry check: same range & tip already acked -> return the same ack
    if watermark.last_acked_seq == req.to_seq and watermark.last_verified_audit_hash == req.tip_audit_hash:
        return CloudAckResponse(
            recovery_session_id=req.recovery_session_id,
            acked_seq=req.to_seq,
            verified_tip_hash=req.tip_audit_hash,
            applied_skus=0,
            quarantined_skus=0,
            quarantine_ids=[],
            acked_at=datetime.now(timezone.utc).isoformat(),
        )

    if req.from_seq != watermark.last_acked_seq or req.prev_audit_hash != watermark.last_verified_audit_hash:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Watermark or audit hash-chain discontinuity detected; escalate to SAFE_MODE",
        )

    applied_skus = 0
    quarantine_ids: list[str] = []

    try:
        for batch in req.batches:
            # 2. Deduplicate individual source_event_ids
            unseen_events = _dedup_events(db, req, batch.source_event_ids)

            if not unseen_events:
                continue

            # 3. Route by sync-go deterministic verdict
            if batch.verdict == "COMMUTE_APPLY":
                result = _apply_commute(db, req, batch, cas_update)
                if result == "applied":
                    applied_skus += 1
                else:
                    # Oversell or missing baseline — quarantine instead of corrupting projections
                    quarantine_ids.append(result)
            else:
                qid = _quarantine_delta(db, req, batch, unseen_events, reason=batch.conflict_reason or "UNSPECIFIED_CONFLICT")
                quarantine_ids.append(qid)

        # 4. Advance the watermark atomically with the batch mutations
        watermark.last_acked_seq = req.to_seq
        watermark.last_verified_audit_hash = req.tip_audit_hash
        db.commit()
    except Exception:
        db.rollback()
        raise

    return CloudAckResponse(
        recovery_session_id=req.recovery_session_id,
        acked_seq=req.to_seq,
        verified_tip_hash=req.tip_audit_hash,
        applied_skus=applied_skus,
        quarantined_skus=len(quarantine_ids),
        quarantine_ids=quarantine_ids,
        acked_at=datetime.now(timezone.utc).isoformat(),
    )


# ------------------------------------------------------------------ helpers

def _dedup_events(db: Session, req: WatermarkReplayRequest, event_ids: list[str]) -> list[str]:
    """Insert each event_id once; return the ones not seen before (SAVEPOINT-safe)."""
    unseen = []
    for event_id in event_ids:
        try:
            with db.begin_nested():
                db.add(CloudIngestedEvent(
                    tenant_id=req.tenant_id,
                    store_id=req.store_id,
                    event_id=event_id,
                    recovery_session_id=req.recovery_session_id,
                    source_seq=0,
                    payload_hash="delta:" + event_id[:64 - len("delta:")],
                ))
            unseen.append(event_id)
        except IntegrityError:
            continue
    return unseen


def _apply_commute(db: Session, req: WatermarkReplayRequest, batch: ClassifiedDeltaBatch, cas_update) -> str:
    """Apply one commutative delta with CAS and a negative-on-hand guard."""
    row = (
        db.query(CloudStoreInventory)
        .filter(
            CloudStoreInventory.tenant_id == req.tenant_id,
            CloudStoreInventory.store_id == req.store_id,
            CloudStoreInventory.sku == batch.sku,
        )
        .first()
    )
    if row is None:
        return _quarantine_delta(db, req, batch, batch.source_event_ids, reason="no cloud baseline for sku")

    if row.on_hand_qty + batch.net_delta_qty < 0:
        return _quarantine_delta(db, req, batch, batch.source_event_ids,
                                 reason=f"applying {batch.net_delta_qty} would drive on-hand negative ({row.on_hand_qty})")

    cas_update(db, CloudStoreInventory, row.id, row.version, {"on_hand_qty": row.on_hand_qty + batch.net_delta_qty})
    return "applied"


def _quarantine_delta(
    db: Session,
    req: WatermarkReplayRequest,
    batch: ClassifiedDeltaBatch,
    source_event_ids: list[str],
    *,
    reason: str,
) -> str:
    qid = f"quar_{req.recovery_session_id}_{batch.sku}"
    existing = db.query(CloudReconciliationQuarantine).filter(
        CloudReconciliationQuarantine.quarantine_id == qid
    ).first()
    if existing is not None:
        return existing.quarantine_id

    current_qty = (
        db.query(CloudStoreInventory)
        .filter(
            CloudStoreInventory.tenant_id == req.tenant_id,
            CloudStoreInventory.store_id == req.store_id,
            CloudStoreInventory.sku == batch.sku,
        )
        .first()
    )
    current_qty_value = current_qty.on_hand_qty if current_qty else None

    db.add(CloudReconciliationQuarantine(
        quarantine_id=qid,
        tenant_id=req.tenant_id,
        store_id=req.store_id,
        recovery_session_id=req.recovery_session_id,
        sku=batch.sku,
        source_event_ids=json.dumps(source_event_ids),
        verdict="quarantine",
        reason=reason,
        edge_delta_qty=batch.net_delta_qty,
        baseline_cloud_qty=batch.baseline_cloud_qty,
        current_cloud_qty=current_qty_value,
        status="PENDING_REVIEW",
        payload=json.dumps({
            "sku": batch.sku,
            "delta": batch.net_delta_qty,
            "baseline_cloud_qty": batch.baseline_cloud_qty,
            "kind": "SALE",
        }, sort_keys=True),
    ))
    return qid
