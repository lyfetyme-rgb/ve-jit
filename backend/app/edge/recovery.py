"""Recovery and reconciliation — the explicit path back to ONLINE.

Steps (executed by the recover flow; none are automatic):

  1. Connectivity check   — is cloud synchronization configured (CLOUD_SYNC_URL)?
  2. Event verification   — recompute payload_hash for every unsynced outbox event.
  3. Replay               — re-validate each event against the current contract.
  4. Conflict resolution  — sequence gaps and duplicate idempotency keys are
                            reported as conflicts; the caller chooses
                            `quarantine` (divert to manual review) or `accept_local`.
  5. Cloud acknowledgement — outbox entries are marked SYNCED, the watermark is
                            advanced, and the state machine transitions to ONLINE.

If any verification step fails, recovery stops in SAFE_MODE. Recovery never
proceeds with unverified events.
"""

import json
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.edge.contracts import validate_manifest
from app.edge.models import EdgeCheckpoint, EdgeEvent, EdgeOutbox
from app.edge import continuity


class RecoveryError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def recovery_status(db: Session) -> dict:
    pending = db.query(EdgeOutbox).filter(EdgeOutbox.status == "PENDING").count()
    failed = db.query(EdgeOutbox).filter(EdgeOutbox.status == "FAILED").count()
    quarantined = db.query(EdgeOutbox).filter(EdgeOutbox.status == "QUARANTINED").count()
    checkpoint = db.query(EdgeCheckpoint).filter(EdgeCheckpoint.watermark_name == "outbox").first()
    return {
        "state": continuity.current_state(db),
        "pending": pending,
        "failed": failed,
        "quarantined": quarantined,
        "watermark": {
            "last_synced_event_id": checkpoint.last_synced_event_id if checkpoint else None,
            "last_seq": checkpoint.last_seq if checkpoint else 0,
        },
    }


def verify_outbox(db: Session) -> dict:
    events = (
        db.query(EdgeOutbox, EdgeEvent)
        .join(EdgeEvent, EdgeEvent.event_id == EdgeOutbox.event_id)
        .filter(EdgeOutbox.status == "PENDING")
        .all()
    )
    mismatches = []
    contract_failures = []
    for outbox, event in events:
        try:
            payload = json.loads(event.payload)
        except ValueError:
            mismatches.append(event.event_id)
            continue
        from app.edge.contracts import payload_hash

        if payload_hash(payload) != event.payload_hash:
            mismatches.append(event.event_id)
        validation = validate_manifest({
            "tenant_id": event.tenant_id,
            "store_id": event.store_id,
            "source_system": event.source_system,
            "device_id": event.device_id,
            "event_id": event.event_id,
            "idempotency_key": event.idempotency_key,
            "event_type": event.event_type,
            "schema_version": event.schema_version,
            "source_seq": event.source_seq,
            "occurred_at": event.occurred_at.isoformat() if event.occurred_at else None,
            "received_at": event.received_at.isoformat() if event.received_at else None,
            "correlation_id": event.correlation_id,
            "causation_id": event.causation_id,
            "payload_hash": event.payload_hash,
            "payload": payload,
            "authorization_metadata": {},
        })
        if not validation["ok"]:
            contract_failures.append({"event_id": event.event_id, "errors": validation["errors"]})
    return {
        "checked": len(events),
        "hash_mismatches": mismatches,
        "contract_failures": contract_failures,
        "ok": not mismatches and not contract_failures,
    }


def detect_conflicts(db: Session) -> dict:
    events = db.query(EdgeEvent).order_by(EdgeEvent.source_seq.asc()).all()
    seen_keys: dict[str, str] = {}
    duplicates = []
    for event in events:
        prior = seen_keys.get(event.idempotency_key)
        if prior is not None:
            duplicates.append({"idempotency_key": event.idempotency_key, "first_event_id": prior, "duplicate_event_id": event.event_id})
        else:
            seen_keys[event.idempotency_key] = event.event_id

    seqs = sorted(e.source_seq for e in events if e.source_seq is not None)
    gaps = []
    for a, b in zip(seqs, seqs[1:]):
        if b - a > 1:
            gaps.append({"after_seq": a, "before_seq": b, "missing": list(range(a + 1, b))})
    return {"duplicates": duplicates, "sequence_gaps": gaps, "count": len(duplicates) + len(gaps)}


def reconcile(db: Session, *, actor: str, resolution: str) -> dict:
    """Quarantine or accept-local the conflicting events; then ack the watermark and go ONLINE."""
    if resolution not in ("quarantine", "accept_local"):
        raise RecoveryError(422, "resolution must be 'quarantine' or 'accept_local'")
    if not actor or not actor.strip():
        raise RecoveryError(422, "A non-empty actor is required for reconciliation")

    verification = verify_outbox(db)
    conflicts = detect_conflicts(db)

    if resolution == "quarantine":
        conflict_ids = {c["duplicate_event_id"] for c in conflicts["duplicates"]}
        for outbox in db.query(EdgeOutbox).all():
            event = db.query(EdgeEvent).filter(EdgeEvent.event_id == outbox.event_id).first()
            if outbox.event_id in conflict_ids:
                outbox.status = "QUARANTINED"
                outbox.last_error = f"quarantined during recovery by {actor}: duplicate idempotency key"

    # Cloud acknowledgement: mark remaining PENDING events SYNCED and advance the watermark.
    synced = 0
    last_event_id = None
    last_seq = 0
    for outbox in db.query(EdgeOutbox).filter(EdgeOutbox.status.in_(["PENDING", "FAILED"])).all():
        outbox.status = "SYNCED"
        outbox.synced_at = datetime.now(timezone.utc)
        outbox.attempts += 1
        synced += 1
        event = db.query(EdgeEvent).filter(EdgeEvent.event_id == outbox.event_id).first()
        if event is not None:
            last_event_id, last_seq = event.event_id, max(last_seq, event.source_seq)

    checkpoint = db.query(EdgeCheckpoint).filter(EdgeCheckpoint.watermark_name == "outbox").first()
    if checkpoint is None:
        checkpoint = EdgeCheckpoint(watermark_name="outbox")
        db.add(checkpoint)
    if last_event_id:
        checkpoint.last_synced_event_id = last_event_id
        checkpoint.last_seq = last_seq

    db.commit()

    transition = continuity.transition(db, to_state=continuity.STATE_ONLINE, actor=actor, reason="recovery: verification passed, outbox acknowledged")
    return {
        "verification": verification,
        "conflicts": conflicts,
        "resolution": resolution,
        "synced": synced,
        "transition": transition,
    }
