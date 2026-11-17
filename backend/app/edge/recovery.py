"""Recovery and reconciliation — the explicit, gated path back to ONLINE.

Four strictly ordered steps, enforced by a RecoverySession state machine.
None may be skipped or reordered:

  1. connectivity_check  — is cloud synchronization configured and reachable?
                           (without CLOUD_SYNC_URL, recovery may only proceed
                           with an explicit ack_mode='local_override' at the
                           final step — a human acknowledges there is no sink)
  2. hash_verification   — recompute payload_hash for every pending outbox event
  3. watermark_replay    — re-validate every event against the contract; report
                           duplicates and sequence gaps for resolution
  4. cloud_ack           — mark pending events SYNCED, advance the watermark
                           checkpoint, complete the session, then ONLINE

Any step that fails moves the node to SAFE_MODE. ONLINE is impossible without a
COMPLETED session — enforced by the continuity state machine itself.
"""

import json
from datetime import datetime, timezone
from typing import Optional

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.edge import continuity
from app.edge.contracts import payload_hash, validate_manifest
from app.edge.models import EdgeCheckpoint, EdgeEvent, EdgeOutbox, RECOVERY_STEPS, RecoverySession

STEP_CONNECTIVITY_CHECK = "CONNECTIVITY_CHECK"
STEP_HASH_VERIFICATION = "HASH_VERIFICATION"
STEP_WATERMARK_REPLAY = "WATERMARK_REPLAY"
STEP_CLOUD_ACK = "CLOUD_ACK"


class RecoveryError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


# ------------------------------------------------------------------ sessions

def _latest_session(db: Session) -> Optional[RecoverySession]:
    return db.query(RecoverySession).order_by(RecoverySession.id.desc()).first()


def _begin_or_resume(db: Session, actor: str) -> RecoverySession:
    state = continuity.current_state(db)
    if state != continuity.STATE_RECOVERING:
        raise RecoveryError(409, f"Recovery runs only from RECOVERING (current: {state}). Transition to RECOVERING first.")
    existing = _latest_session(db)
    if existing is not None and existing.outcome == "IN_PROGRESS":
        return existing
    session = RecoverySession(actor=actor.strip(), outcome="IN_PROGRESS", state=STEP_CONNECTIVITY_CHECK, steps="{}")
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def _require_step(session: RecoverySession, expected: str) -> None:
    if session.outcome != "IN_PROGRESS":
        raise RecoveryError(409, f"Recovery session {session.id} already {session.outcome}. Start a new one from RECOVERING.")
    if session.state != expected:
        raise RecoveryError(
            409,
            f"Recovery is sequential: expected step {expected}, but session is at {session.state}. "
            "Run the steps in order: connectivity_check -> hash_verification -> watermark_replay -> cloud_ack.",
        )


def _record_step(db: Session, session: RecoverySession, step: str, result: dict) -> None:
    steps = json.loads(session.steps or "{}")
    steps[step] = {
        "ok": result.get("ok", False),
        "at": datetime.now(timezone.utc).isoformat(),
        "detail": result,
    }
    session.steps = json.dumps(steps, sort_keys=True)
    next_step = RECOVERY_STEPS[RECOVERY_STEPS.index(step) + 1] if step != STEP_CLOUD_ACK else STEP_CLOUD_ACK
    if result.get("ok", False):
        session.state = next_step
    db.commit()
    db.refresh(session)


def _fail_step(db: Session, session: RecoverySession, step: str, detail: str) -> None:
    steps = json.loads(session.steps or "{}")
    steps[step] = {"ok": False, "at": datetime.now(timezone.utc).isoformat(), "detail": detail}
    session.steps = json.dumps(steps, sort_keys=True)
    session.outcome = "FAILED"
    db.commit()
    # An integrity or safety violation moves the node to SAFE_MODE — from RECOVERING.
    continuity.transition(
        db, to_state=continuity.STATE_SAFE_MODE, actor=session.actor,
        reason=f"recovery step {step.lower()} failed: {detail[:200]}",
    )


# --------------------------------------------------------------- status

def recovery_status(db: Session) -> dict:
    pending = db.query(EdgeOutbox).filter(EdgeOutbox.status == "PENDING").count()
    failed = db.query(EdgeOutbox).filter(EdgeOutbox.status == "FAILED").count()
    quarantined = db.query(EdgeOutbox).filter(EdgeOutbox.status == "QUARANTINED").count()
    checkpoint = db.query(EdgeCheckpoint).filter(EdgeCheckpoint.watermark_name == "outbox").first()
    latest = _latest_session(db)
    return {
        "state": continuity.current_state(db),
        "pending": pending,
        "failed": failed,
        "quarantined": quarantined,
        "watermark": {
            "last_synced_event_id": checkpoint.last_synced_event_id if checkpoint else None,
            "last_seq": checkpoint.last_seq if checkpoint else 0,
        },
        "session": session_to_dict(latest) if latest else None,
    }


def session_to_dict(session: RecoverySession) -> dict:
    return {
        "id": session.id,
        "actor": session.actor,
        "state": session.state,
        "steps": json.loads(session.steps or "{}"),
        "outcome": session.outcome,
    }


def latest_recovery_completed(db: Session) -> bool:
    latest = _latest_session(db)
    return latest is not None and latest.outcome == "COMPLETED"


# --------------------------------------------------------------- step 1

def connectivity_check(db: Session, *, actor: str) -> dict:
    if not actor or not actor.strip():
        raise RecoveryError(422, "A non-empty actor is required")
    session = _begin_or_resume(db, actor)
    _require_step(session, STEP_CONNECTIVITY_CHECK)

    url = (settings.CLOUD_SYNC_URL or "").strip()
    if not url:
        result = {"ok": True, "mode": "local_only", "detail": "CLOUD_SYNC_URL not configured; cloud_ack must use ack_mode='local_override'"}
        _record_step(db, session, STEP_CONNECTIVITY_CHECK, result)
        return {"step": STEP_CONNECTIVITY_CHECK, **result, "session": session_to_dict(session)}

    try:
        probe = httpx.get(url, timeout=5.0)
        reachable = probe.status_code < 500
    except httpx.HTTPError as exc:
        reachable = False
        probe_detail = f"{type(exc).__name__}: {exc}"
    else:
        probe_detail = f"HTTP {probe.status_code}"

    if not reachable:
        _record_step(db, session, STEP_CONNECTIVITY_CHECK, {"ok": False, "detail": probe_detail})
        _fail_step(db, session, STEP_CONNECTIVITY_CHECK, f"cloud endpoint unreachable: {probe_detail}")
        raise RecoveryError(409, f"Connectivity check failed ({probe_detail}); node moved to SAFE_MODE")

    result = {"ok": True, "mode": "cloud", "detail": f"cloud endpoint reachable ({probe_detail})"}
    _record_step(db, session, STEP_CONNECTIVITY_CHECK, result)
    return {"step": STEP_CONNECTIVITY_CHECK, **result, "session": session_to_dict(session)}


# --------------------------------------------------------------- step 2

def hash_verification(db: Session, *, actor: str) -> dict:
    if not actor or not actor.strip():
        raise RecoveryError(422, "A non-empty actor is required")
    session = _begin_or_resume(db, actor)
    _require_step(session, STEP_HASH_VERIFICATION)

    verification = verify_outbox(db)
    if not verification["ok"]:
        detail = f"{len(verification['hash_mismatches'])} hash mismatch(es), {len(verification['contract_failures'])} contract failure(s)"
        _record_step(db, session, STEP_HASH_VERIFICATION, {"ok": False, "detail": verification})
        _fail_step(db, session, STEP_HASH_VERIFICATION, detail)
        raise RecoveryError(409, f"Hash verification failed: {detail}; node moved to SAFE_MODE")

    _record_step(db, session, STEP_HASH_VERIFICATION, {"ok": True, "detail": verification})
    return {"step": STEP_HASH_VERIFICATION, "ok": True, "verification": verification, "session": session_to_dict(session)}


# --------------------------------------------------------------- step 3

def watermark_replay(db: Session, *, actor: str) -> dict:
    if not actor or not actor.strip():
        raise RecoveryError(422, "A non-empty actor is required")
    session = _begin_or_resume(db, actor)
    _require_step(session, STEP_WATERMARK_REPLAY)

    replay = replay_from_checkpoint(db)
    _record_step(db, session, STEP_WATERMARK_REPLAY, {"ok": True, "detail": replay})
    return {"step": STEP_WATERMARK_REPLAY, "ok": True, "replay": replay, "session": session_to_dict(session)}


def replay_from_checkpoint(db: Session) -> dict:
    """Re-validate contract + hash integrity of every pending event and report
    duplicate idempotency keys and source_seq gaps for the ack step to resolve."""
    verification = verify_outbox(db)
    conflicts = detect_conflicts(db)
    return {
        "replayed": verification["checked"],
        "verification": {k: v for k, v in verification.items() if k != "ok"},
        "conflicts": conflicts,
        "ok": True,
    }


# --------------------------------------------------------------- step 4

def cloud_ack(db: Session, *, actor: str, resolution: str, ack_mode: str) -> dict:
    if not actor or not actor.strip():
        raise RecoveryError(422, "A non-empty actor is required")
    if resolution not in ("quarantine", "accept_local"):
        raise RecoveryError(422, "resolution must be 'quarantine' or 'accept_local'")
    if ack_mode not in ("cloud", "local_override"):
        raise RecoveryError(422, "ack_mode must be 'cloud' or 'local_override'")
    session = _begin_or_resume(db, actor)
    _require_step(session, STEP_CLOUD_ACK)

    url = (settings.CLOUD_SYNC_URL or "").strip()
    if ack_mode == "cloud":
        if not url:
            raise RecoveryError(409, "ack_mode='cloud' requires CLOUD_SYNC_URL to be configured")
        try:
            probe = httpx.get(url.split('/api')[0] + '/health', timeout=5.0)
            if probe.status_code >= 500:
                raise RecoveryError(409, f"Cloud endpoint returned {probe.status_code}; refusing to ack")
        except httpx.HTTPError as exc:
            raise RecoveryError(409, f"Cloud endpoint unreachable ({exc}); refusing to ack")
    # local_override: a named human acknowledges there is no cloud sink to drain to.

    # Real cloud delivery: push the batch to the cloud recovery ingestion API
    # when a URL is configured. Local_override means nothing is shipped anywhere.
    ingestion_report = None
    if ack_mode == "cloud":
        manifests = _pending_manifests(db)
        try:
            resp = httpx.post(
                url.rstrip("/") + "/api/v1/cloud/recovery/sessions",
                json={
                    "recovery_session_id": str(session.id),
                    "tenant_id": settings.TENANT_ID,
                    "store_id": settings.STORE_ID,
                    "events": manifests,
                    "verdicts": [],
                    "actor": actor.strip(),
                },
                headers={"content-type": "application/json", "x-vejit-actor": actor.strip()},
                timeout=30.0,
            )
        except httpx.HTTPError as exc:
            _fail_step(db, session, STEP_CLOUD_ACK, f"cloud delivery failed: {exc}")
            raise RecoveryError(409, f"Cloud delivery failed ({exc}); node moved to SAFE_MODE") from exc
        if resp.status_code >= 400:
            detail = f"cloud ingestion returned {resp.status_code}: {resp.text[:500]}"
            _fail_step(db, session, STEP_CLOUD_ACK, detail)
            raise RecoveryError(409, detail)
        try:
            ingestion_report = resp.json()
        except ValueError:
            ingestion_report = {"raw": resp.text[:500]}

    conflicts = detect_conflicts(db)
    quarantined = 0
    if resolution == "quarantine":
        duplicate_ids = {c["duplicate_event_id"] for c in conflicts["duplicates"]}
        for outbox in db.query(EdgeOutbox).filter(EdgeOutbox.event_id.in_(duplicate_ids)).all():
            outbox.status = "QUARANTINED"
            outbox.last_error = f"quarantined during recovery by {actor.strip()}: duplicate idempotency key"
            quarantined += 1

    synced = 0
    last_event_id = None
    last_seq = 0
    for outbox in db.query(EdgeOutbox).filter(EdgeOutbox.status.in_(["PENDING", "FAILED"])).all():
        event = db.query(EdgeEvent).filter(EdgeEvent.event_id == outbox.event_id).first()
        outbox.status = "SYNCED"
        outbox.synced_at = datetime.now(timezone.utc)
        outbox.attempts += 1
        synced += 1
        if event is not None:
            last_event_id, last_seq = event.event_id, max(last_seq, event.source_seq)

    checkpoint = db.query(EdgeCheckpoint).filter(EdgeCheckpoint.watermark_name == "outbox").first()
    if checkpoint is None:
        checkpoint = EdgeCheckpoint(watermark_name="outbox", last_seq=0)
        db.add(checkpoint)
    if last_event_id:
        checkpoint.last_synced_event_id = last_event_id
        checkpoint.last_seq = last_seq

    session.outcome = "COMPLETED"
    steps = json.loads(session.steps or "{}")
    steps[STEP_CLOUD_ACK] = {
        "ok": True,
        "at": datetime.now(timezone.utc).isoformat(),
        "detail": {"synced": synced, "quarantined": quarantined, "resolution": resolution, "ack_mode": ack_mode},
    }
    session.steps = json.dumps(steps, sort_keys=True)
    db.commit()
    db.refresh(session)

    transition = continuity.transition(
        db, to_state=continuity.STATE_ONLINE, actor=actor, reason="recovery complete: connectivity check, hash verification, watermark replay and cloud ack",
    )
    return {
        "step": STEP_CLOUD_ACK,
        "ok": True,
        "synced": synced,
        "quarantined": quarantined,
        "resolution": resolution,
        "ack_mode": ack_mode,
        "ingestion": ingestion_report,
        "watermark": {"last_synced_event_id": last_event_id, "last_seq": last_seq},
        "transition": transition,
        "session": session_to_dict(session),
    }


def reconcile(db: Session, *, actor: str, resolution: str, ack_mode: str = "local_override") -> dict:
    """Convenience: run the full gated sequence in order. Prefer the explicit
    four-step endpoints; this exists for operators who want one command."""
    connectivity_check(db, actor=actor)
    hash_verification(db, actor=actor)
    watermark_replay(db, actor=actor)
    return cloud_ack(db, actor=actor, resolution=resolution, ack_mode=ack_mode)


# --------------------------------------------------------------- primitives

def _pending_manifests(db: Session) -> list[dict]:
    """Rebuild full manifests for all PENDING/FAILED outbox events (for cloud delivery)."""
    rows = (
        db.query(EdgeOutbox, EdgeEvent)
        .join(EdgeEvent, EdgeEvent.event_id == EdgeOutbox.event_id)
        .filter(EdgeOutbox.status.in_(["PENDING", "FAILED"]))
        .all()
    )
    manifests = []
    for _, event in rows:
        manifests.append({
            "tenant_id": event.tenant_id,
            "store_id": event.store_id,
            "source_system": event.source_system,
            "source_device_id": event.source_device_id,
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
            "payload": json.loads(event.payload),
            "auth_metadata": json.loads(event.auth_metadata) if event.auth_metadata else {},
        })
    return manifests


def verify_outbox(db: Session) -> dict:
    events = (
        db.query(EdgeOutbox, EdgeEvent)
        .join(EdgeEvent, EdgeEvent.event_id == EdgeOutbox.event_id)
        .filter(EdgeOutbox.status.in_(["PENDING", "FAILED"]))
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
        if payload_hash(payload) != event.payload_hash:
            mismatches.append(event.event_id)
        validation = validate_manifest({
            "tenant_id": event.tenant_id,
            "store_id": event.store_id,
            "source_system": event.source_system,
            "source_device_id": event.source_device_id,
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
            "auth_metadata": {},
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
            duplicates.append({
                "idempotency_key": event.idempotency_key,
                "first_event_id": prior,
                "duplicate_event_id": event.event_id,
            })
        else:
            seen_keys[event.idempotency_key] = event.event_id

    seqs = sorted(e.source_seq for e in events if e.source_seq is not None)
    gaps = []
    for a, b in zip(seqs, seqs[1:]):
        if b - a > 1:
            gaps.append({"after_seq": a, "before_seq": b, "missing": list(range(a + 1, b))})
    return {"duplicates": duplicates, "sequence_gaps": gaps, "count": len(duplicates) + len(gaps)}
