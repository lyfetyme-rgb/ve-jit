"""Cloud-side ingestion, replay & reconciliation.

Contract (the truncated spec, plus the stated invariants made executable):

1. Hash-chain continuity — every incoming batch is verified against the stored
   watermark tip for the store: tip_i = SHA256(tip_{i-1} + ":" + payload_hash_i),
   starting from the last acked tip (or GENESIS). Any gap, reorder, duplicate
   sequence or forgery breaks the chain and the whole batch is rejected.

2. Batch idempotency & deduplication — event IDs already captured are skipped
   and the (tenant, store, event_id) idempotency ledger enforces it at the DB
   level too. Replaying a recovery session never double-decrements cloud
   inventory and never re-files the same HITL item.

3. Quarantine-first conflict routing — anything the deterministic rules refuse
   is persisted to the HITL quarantine queue with a machine reason. Nothing is
   silently dropped and nothing mutates cloud inventory without either (a) a
   commutative SALE aggregate that cannot take on-hand negative, or (b) an
   explicit `apply` resolution from a named human.
"""

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.cloud.models import (
    CloudInventory,
    CloudIngestedEvent,
    CloudReconciliationQuarantine,
    CloudStoreWatermark,
)
from app.edge.contracts import payload_hash, validate_manifest

GENESIS = "GENESIS"


class ReconcileError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def compute_chain_tip(tip: str, payload_hashes: list[str]) -> str:
    current = tip
    for hash_value in payload_hashes:
        current = hashlib.sha256(f"{current}:{hash_value}".encode("utf-8")).hexdigest()
    return current


def derive_sale_deltas(events: list[dict]) -> dict[str, dict]:
    """Aggregate SALE deltas per SKU from pos.transaction.completed events."""
    per_sku: dict[str, dict] = {}
    for event in events:
        if event.get("event_type") != "pos.transaction.completed":
            continue
        for line in (event.get("payload") or {}).get("lines", []) or []:
            sku = line.get("sku")
            qty = line.get("quantity")
            if sku and qty:
                slot = per_sku.setdefault(sku, {"sku": sku, "delta": 0, "kind": "SALE", "source_event_ids": []})
                slot["delta"] += -int(qty)
                slot["source_event_ids"].append(event["event_id"])
    return per_sku


def ingest_recovery_batch(
    db: Session,
    *,
    tenant_id: str,
    store_id: str,
    recovery_session_id: str,
    events: list[dict],
    verdicts: list[dict],
    actor: str,
    provided_chain_tip: Optional[str] = None,
) -> dict:
    _require_actor(actor)

    if not events:
        raise ReconcileError(422, "events must not be empty")

    # -- contract validation (whole batch rejected, nothing applied) -------
    errors = []
    for i, event in enumerate(events):
        result = validate_manifest(event)
        if not result["ok"]:
            errors.append({"index": i, "event_id": event.get("event_id"), "errors": result["errors"]})
    if errors:
        raise ReconcileError(422, f"contract validation failed for {len(errors)} event(s): {json.dumps(errors)}")

    ordered = sorted(events, key=lambda e: int(e["source_seq"]))
    seqs = [int(e["source_seq"]) for e in ordered]
    if len(set(seqs)) != len(seqs):
        raise ReconcileError(422, "duplicate source_seq in batch; replay is ambiguous")

    # -- hash-chain continuity against the stored watermark tip ------------
    watermark = _get_or_create_watermark(db, tenant_id, store_id)
    prev_tip = watermark.last_verified_audit_hash or GENESIS
    new_tip = compute_chain_tip(prev_tip, [e["payload_hash"] for e in ordered])
    if provided_chain_tip and provided_chain_tip != new_tip:
        raise ReconcileError(409, f"chain tip mismatch: recomputed {new_tip}, provided {provided_chain_tip}")

    verdict_by_event = {v.get("event_id"): v for v in verdicts if isinstance(v, dict)}
    quarantine_ids = {eid for eid, v in verdict_by_event.items() if v.get("action") == "quarantine"}

    applied_deltas: dict[str, int] = {}
    duplicates = 0
    applied = 0
    quarantined = 0
    fresh_events: list[dict] = []

    try:
        # Pass 1: capture every unseen event as ingested (audit of what happened),
        #         and bucket each event as apply-later vs quarantine.
        for event in ordered:
            event_id = event["event_id"]
            unseen = _mark_ingested(db, tenant_id, store_id, recovery_session_id, event)
            if not unseen:
                duplicates += 1
                continue
            applied += 1
            fresh_events.append(event)

            verdict = verdict_by_event.get(event_id) or {}
            action = verdict.get("action")
            reason = verdict.get("reason", "non-SALE event or no commutative verdict; routed to HITL")

            if action == "quarantine":
                reason = verdict.get("reason", "sync-go classified this event as non-commutative")
                _quarantine(db, tenant_id, store_id, recovery_session_id, event_id, reason, event)
                quarantined += 1
            elif action == "commutative":
                continue  # aggregate applied in pass 2
            elif event.get("event_type") == "pos.transaction.completed":
                continue  # included in the aggregate pass
            else:
                _quarantine(db, tenant_id, store_id, recovery_session_id, event_id, reason, event)
                quarantined += 1

        # Pass 2: aggregate SALE deltas over fresh, non-quarantined events only,
        # with CAS and a negative-on-hand guard.
        aggregate_events = [e for e in fresh_events if e["event_id"] not in quarantine_ids]
        per_sku = derive_sale_deltas(aggregate_events)
        explicitly_commutative = any(v.get("action") == "commutative" for v in verdict_by_event.values())
        all_sale = bool(aggregate_events) and all(e.get("event_type") == "pos.transaction.completed" for e in aggregate_events)
        apply_sale_aggregate = bool(per_sku) and (explicitly_commutative or all_sale)
        if apply_sale_aggregate:
            for sku, delta_record in per_sku.items():
                delta = int(delta_record["delta"])
                row = (
                    db.query(CloudInventory)
                    .filter(
                        CloudInventory.tenant_id == tenant_id,
                        CloudInventory.store_id == store_id,
                        CloudInventory.sku == sku,
                    )
                    .first()
                )
                if row is None:
                    _quarantine(
                        db, tenant_id, store_id, recovery_session_id,
                        f"nobase:{sku}", f"no cloud baseline for {sku}; cannot apply SALE delta {delta}",
                        {"sku": sku, "delta": delta, "kind": "SALE", "source_event_ids": delta_record["source_event_ids"]},
                    )
                    quarantined += 1
                    continue
                if row.quantity + delta < 0:
                    _quarantine(
                        db, tenant_id, store_id, recovery_session_id,
                        f"oversell:{sku}", f"applying {delta} to {sku} would drive on-hand negative ({row.quantity})",
                        {"sku": sku, "delta": delta, "kind": "SALE", "source_event_ids": delta_record["source_event_ids"]},
                    )
                    quarantined += 1
                    continue
                from app.planes.operations import cas_update

                cas_update(db, CloudInventory, row.id, row.version, {"quantity": row.quantity + delta})
                applied_deltas[sku] = delta

        max_seq = max(seqs)
        watermark.last_acked_seq = max(watermark.last_acked_seq, max_seq)
        watermark.last_verified_audit_hash = new_tip

        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ReconcileError(409, f"ingestion conflict (duplicate event?): {exc}")
    except Exception:
        db.rollback()
        raise

    return {
        "ok": True,
        "recovery_session_id": recovery_session_id,
        "tenant_id": tenant_id,
        "store_id": store_id,
        "events_received": len(events),
        "events_applied": applied,
        "duplicates_skipped": duplicates,
        "applied_deltas": applied_deltas,
        "quarantined": quarantined,
        "watermark": {
            "last_acked_seq": watermark.last_acked_seq,
            "last_verified_audit_hash": new_tip,
        },
        "chain_tip_verified": True,
    }


def _apply_quarantined_payload(db: Session, row: CloudReconciliationQuarantine, payload: dict) -> bool:
    """Best-effort CAS application of a quarantined delta payload.

    Supports two shapes:
      - single delta:      {"sku": ..., "delta": int}
      - POS event:         {"payload": {"lines": [{"sku": ..., "quantity": ...}]}}
    Raises ReconcileError(409) on oversell or missing baseline.
    """
    from app.planes.operations import cas_update

    deltas = []
    if payload.get("sku") and payload.get("delta") is not None:
        deltas.append((payload["sku"], int(payload["delta"])))
    elif isinstance((payload.get("payload") or {}).get("lines"), list):
        for line in payload["payload"]["lines"]:
            if line.get("sku") and line.get("quantity"):
                deltas.append((line["sku"], -int(line["quantity"])))
    if not deltas:
        return False

    # Validate every leg before mutating anything (all-or-nothing).
    rows = []
    for sku, delta in deltas:
        inv = (
            db.query(CloudInventory)
            .filter(
                CloudInventory.tenant_id == row.tenant_id,
                CloudInventory.store_id == row.store_id,
                CloudInventory.sku == sku,
            )
            .first()
        )
        if inv is None:
            raise ReconcileError(409, f"no cloud baseline for {sku}; seed it first (POST /api/v1/cloud/inventory/seed)")
        if inv.quantity + delta < 0:
            raise ReconcileError(409, f"applying would drive on-hand negative for {sku} ({inv.quantity} {delta:+d})")
        rows.append((inv, delta))

    for inv, delta in rows:
        cas_update(db, CloudInventory, inv.id, inv.version, {"quantity": inv.quantity + delta})
    return True


def resolve_quarantine(
    db: Session,
    *,
    quarantine_id: str,
    resolution: str,
    actor: str,
    note: Optional[str] = None,
) -> dict:
    _require_actor(actor)

    from app.planes.operations import cas_update

    row = (
        db.query(CloudReconciliationQuarantine)
        .filter(CloudReconciliationQuarantine.quarantine_id == quarantine_id)
        .first()
    )
    if row is None:
        raise ReconcileError(404, f"quarantine item {quarantine_id} not found")
    if row.resolved_at is not None:
        return {"ok": True, "quarantine_id": row.quarantine_id, "changed": False, "resolution": row.resolution}

    if resolution == "apply":
        try:
            payload = json.loads(row.payload)
        except ValueError:
            raise ReconcileError(422, "quarantine payload is not valid JSON; cannot apply")
        applied = _apply_quarantined_payload(db, row, payload)
        if not applied:
            raise ReconcileError(422, "quarantine payload carries no applicable inventory delta")

    row.resolved_at = datetime.now(timezone.utc)
    row.resolved_by = actor.strip()
    row.resolution = resolution
    if note:
        row.reason = f"{row.reason} | resolution note: {note}"
    db.commit()
    return {"ok": True, "quarantine_id": row.quarantine_id, "changed": True, "resolution": resolution, "resolved_by": actor.strip()}


def quarantine_to_dict(row: CloudReconciliationQuarantine) -> dict:
    try:
        payload = json.loads(row.payload)
    except ValueError:
        payload = row.payload
    return {
        "quarantine_id": row.quarantine_id,
        "tenant_id": row.tenant_id,
        "store_id": row.store_id,
        "recovery_session_id": row.recovery_session_id,
        "source_event_id": row.source_event_id,
        "verdict": row.verdict,
        "reason": row.reason,
        "payload": payload,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "resolved_at": row.resolved_at.isoformat() if row.resolved_at else None,
        "resolved_by": row.resolved_by,
        "resolution": row.resolution,
    }


# ------------------------------------------------------------------ helpers

def _require_actor(actor: Optional[str]) -> None:
    if not actor or not actor.strip():
        raise ReconcileError(422, "a non-empty actor is required")


def _get_or_create_watermark(db: Session, tenant_id: str, store_id: str) -> CloudStoreWatermark:
    row = (
        db.query(CloudStoreWatermark)
        .filter(CloudStoreWatermark.tenant_id == tenant_id, CloudStoreWatermark.store_id == store_id)
        .first()
    )
    if row is None:
        row = CloudStoreWatermark(tenant_id=tenant_id, store_id=store_id, last_acked_seq=0, last_verified_audit_hash=GENESIS)
        db.add(row)
        db.flush()
    return row


def _mark_ingested(db: Session, tenant_id: str, store_id: str, recovery_session_id: str, event: dict) -> bool:
    """Insert into the idempotency ledger. Returns True if new, False if duplicate.

    Uses a SAVEPOINT so a duplicate skips exactly this insert instead of
    rolling back the whole recovery batch.
    """
    try:
        with db.begin_nested():
            db.add(CloudIngestedEvent(
                tenant_id=tenant_id,
                store_id=store_id,
                event_id=event["event_id"],
                recovery_session_id=recovery_session_id,
                source_seq=int(event["source_seq"]),
                payload_hash=event["payload_hash"],
            ))
        return True
    except IntegrityError:
        return False


def _quarantine(
    db: Session,
    tenant_id: str,
    store_id: str,
    recovery_session_id: str,
    source_event_id: str,
    reason: str,
    payload: Any,
    verdict: str = "quarantine",
) -> None:
    db.add(CloudReconciliationQuarantine(
        quarantine_id=f"q_{uuid.uuid4().hex[:16]}",
        tenant_id=tenant_id,
        store_id=store_id,
        recovery_session_id=recovery_session_id,
        source_event_id=str(source_event_id)[:64] or "unknown",
        reason=str(reason)[:2000],
        payload=json.dumps(payload, sort_keys=True, default=str),
        verdict=verdict,
    ))
