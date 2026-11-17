"""Vendarix edge API.

The edge node receives hardware and enterprise events, normalizes them into
shared contracts, validates them, runs approved local AI skills, applies
deterministic policies, requests human authorization where required, persists
authorized operational changes, queues for cloud synchronization, and recovers
after connectivity returns.

SAFE_MODE blocks business-action endpoints across the API (enforced in the
operations plane too). Observation paths (ingest, events, continuity, skills
that do not act on the business state) remain available.
"""

import json
from typing import Any, List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import settings
from app.db.database import get_db
from app.edge import continuity, recovery
from app.edge.adapters import AdapterContext, adapter_for
from app.edge.contracts import EVENT_TYPES, envelope_completeness
from app.edge.fabric import event_to_dict, ingest
from app.edge.models import (
    AdapterHeartbeat,
    EdgeEvent,
    EdgeOutbox,
    PolicyRule,
)
from app.edge import policy as policy_engine
from app.edge.skills import SkillError, list_skills, run_skill
from app.planes.governance import create_recommendation

router = APIRouter(prefix="/api/v1/edge", tags=["edge"])


# ------------------------------------------------------------------ schemas

class HeartbeatIn(BaseModel):
    device_id: str = Field(..., min_length=1)
    vendor: str = Field(..., min_length=1)
    interface: str = Field(..., min_length=1)
    healthy: bool = True
    detail: Optional[str] = None


class IngestIn(BaseModel):
    vendor: str = Field(..., min_length=1)
    device_id: str = Field(..., min_length=1)
    tenant_id: Optional[str] = None
    store_id: Optional[str] = None
    source_seq: int = Field(1, ge=0)
    raw: dict[str, Any]
    occurred_at: Optional[str] = None
    correlation_id: Optional[str] = None
    causation_id: Optional[str] = None
    action_class: str = Field("general", max_length=64)


class TransitionIn(BaseModel):
    to_state: str = Field(..., min_length=1)
    actor: str = Field(..., min_length=1)
    reason: str = Field(..., min_length=1)


class PolicyRuleIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    rule: str = Field(..., min_length=1, max_length=64)
    params: dict[str, Any] = Field(default_factory=dict)
    requires_approval: bool = False
    action_class: str = Field("general", max_length=64)
    actor: str = Field(..., min_length=1)


class SeedPoliciesIn(BaseModel):
    actor: str = Field(..., min_length=1)


class SkillRunIn(BaseModel):
    actor: str = Field(..., min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)


class DrainIn(BaseModel):
    actor: str = Field(..., min_length=1)
    max_batch: int = Field(25, ge=1, le=500)


class ReconcileEventIn(BaseModel):
    actor: str = Field(..., min_length=1)


# --------------------------------------------------------------- status

@router.get("/status")
def edge_status(db: Session = Depends(get_db)):
    value = continuity.compute_continuity_value(db)
    outbox_pending = db.query(EdgeOutbox).filter(EdgeOutbox.status == "PENDING").count()
    return {
        "continuity": value,
        "outbox_pending": outbox_pending,
        "cloud_sync_configured": bool(settings.CLOUD_SYNC_URL),
        "approved_skills": sum(1 for s in list_skills() if s["approved"]),
    }


@router.get("/continuity/score")
def continuity_score(db: Session = Depends(get_db)):
    return continuity.compute_continuity_value(db)


@router.post("/continuity/transition")
def continuity_transition(payload: TransitionIn, db: Session = Depends(get_db)):
    try:
        return continuity.transition(db, to_state=payload.to_state.upper(), actor=payload.actor, reason=payload.reason)
    except continuity.ContinuityError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)


# --------------------------------------------------------------- recovery

@router.get("/recovery/status")
def recovery_status(db: Session = Depends(get_db)):
    return recovery.recovery_status(db)


class RecoveryStepIn(BaseModel):
    actor: str = Field(..., min_length=1)


class CloudAckIn(BaseModel):
    actor: str = Field(..., min_length=1)
    resolution: str = Field(..., pattern="^(quarantine|accept_local)$")
    ack_mode: str = Field(..., pattern="^(cloud|local_override)$")


@router.post("/recovery/connectivity-check")
def connectivity_check(payload: RecoveryStepIn, db: Session = Depends(get_db)):
    """Step 1 of the gated recovery sequence."""
    try:
        return recovery.connectivity_check(db, actor=payload.actor)
    except recovery.RecoveryError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)


@router.post("/recovery/hash-verification")
def hash_verification(payload: RecoveryStepIn, db: Session = Depends(get_db)):
    """Step 2 of the gated recovery sequence: recompute payload hashes of every pending event."""
    try:
        return recovery.hash_verification(db, actor=payload.actor)
    except recovery.RecoveryError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)


@router.post("/recovery/watermark-replay")
def watermark_replay(payload: RecoveryStepIn, db: Session = Depends(get_db)):
    """Step 3 of the gated recovery sequence: contract replay + conflict detection."""
    try:
        return recovery.watermark_replay(db, actor=payload.actor)
    except recovery.RecoveryError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)


@router.post("/recovery/cloud-ack")
def cloud_ack(payload: CloudAckIn, db: Session = Depends(get_db)):
    """Step 4 of the gated recovery sequence: resolve conflicts, sync, advance the watermark, go ONLINE."""
    try:
        return recovery.cloud_ack(db, actor=payload.actor, resolution=payload.resolution, ack_mode=payload.ack_mode)
    except recovery.RecoveryError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)


class ReconcileIn(BaseModel):
    actor: str = Field(..., min_length=1)
    resolution: str = Field(..., pattern="^(quarantine|accept_local)$")
    ack_mode: str = Field("local_override", pattern="^(cloud|local_override)$")


@router.post("/recovery/reconcile")
def recovery_reconcile(payload: ReconcileIn, db: Session = Depends(get_db)):
    """Convenience: run the full gated sequence in order (connectivity_check ->
    hash_verification -> watermark_replay -> cloud_ack). Steps are still enforced
    individually; this endpoint just saves four calls."""
    try:
        return recovery.reconcile(db, actor=payload.actor, resolution=payload.resolution, ack_mode=payload.ack_mode)
    except recovery.RecoveryError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)


# --------------------------------------------------------------- devices

@router.post("/devices/heartbeat", status_code=201)
def device_heartbeat(payload: HeartbeatIn, db: Session = Depends(get_db)):
    row = db.query(AdapterHeartbeat).filter(AdapterHeartbeat.device_id == payload.device_id).first()
    if row is None:
        row = AdapterHeartbeat(device_id=payload.device_id, vendor=payload.vendor, interface=payload.interface)
        db.add(row)
    row.vendor = payload.vendor
    row.interface = payload.interface
    row.healthy = payload.healthy
    row.detail = payload.detail
    db.commit()
    return {"device_id": row.device_id, "healthy": row.healthy, "registered": True}


@router.get("/devices")
def list_devices(db: Session = Depends(get_db)):
    rows = db.query(AdapterHeartbeat).order_by(AdapterHeartbeat.device_id.asc()).all()
    return {"devices": [
        {
            "device_id": r.device_id,
            "vendor": r.vendor,
            "interface": r.interface,
            "healthy": r.healthy,
            "detail": r.detail,
            "last_seen": r.last_seen.isoformat() if r.last_seen else None,
        }
        for r in rows
    ]}


# --------------------------------------------------------------- ingest

@router.post("/ingest", status_code=201)
def ingest_event(payload: IngestIn, db: Session = Depends(get_db)):
    try:
        adapter = adapter_for(payload.vendor)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    occurred_at = None
    if payload.occurred_at:
        try:
            from datetime import datetime

            occurred_at = datetime.fromisoformat(payload.occurred_at.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(status_code=422, detail="occurred_at is not a valid ISO-8601 timestamp")

    ctx = AdapterContext(
        tenant_id=payload.tenant_id or settings.TENANT_ID,
        store_id=payload.store_id or settings.STORE_ID,
        source_system=(payload.vendor or "enterprise").lower(),
        device_id=payload.device_id,
        source_seq=payload.source_seq,
        occurred_at=occurred_at,
        correlation_id=payload.correlation_id,
        causation_id=payload.causation_id,
    )
    manifests = adapter.translate(payload.raw, ctx)
    results = [ingest(db, manifest=m, raw_source=payload.vendor, action_class=payload.action_class) for m in manifests]
    return {"events": results}


@router.get("/events")
def list_events(
    route: Optional[str] = None,
    status: Optional[str] = None,
    event_type: Optional[str] = None,
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    query = db.query(EdgeEvent)
    if route:
        query = query.filter(EdgeEvent.route == route)
    if status:
        query = query.filter(EdgeEvent.status == status.upper())
    if event_type:
        query = query.filter(EdgeEvent.event_type == event_type)
    rows = query.order_by(EdgeEvent.id.desc()).limit(limit).all()
    return {"events": [event_to_dict(e) for e in rows]}


@router.get("/events/{event_id}")
def get_event(event_id: str, db: Session = Depends(get_db)):
    event = db.query(EdgeEvent).filter(EdgeEvent.event_id == event_id).first()
    if event is None:
        raise HTTPException(status_code=404, detail=f"Event {event_id} not found")
    return event_to_dict(event)


@router.post("/events/{event_id}/reconcile")
def reconcile_event(event_id: str, payload: ReconcileEventIn, db: Session = Depends(get_db)):
    """Compare an observed inventory count against the operational projection and
    emit an Intelligence recommendation when they diverge. Operations executes
    any resulting action — never automatic."""
    event = db.query(EdgeEvent).filter(EdgeEvent.event_id == event_id).first()
    if event is None:
        raise HTTPException(status_code=404, detail=f"Event {event_id} not found")
    if event.event_type != "inventory.count.observed":
        raise HTTPException(status_code=409, detail=f"Reconciliation is supported for inventory.count.observed, not {event.event_type}")

    try:
        data = json.loads(event.payload)
    except ValueError:
        raise HTTPException(status_code=422, detail="Event payload is not valid JSON")
    sku = data.get("sku")
    counted = data.get("quantity")
    if not sku or counted is None:
        raise HTTPException(status_code=422, detail="Event payload must include sku and quantity")

    from app.db.models import InventoryItem

    item = db.query(InventoryItem).filter(InventoryItem.sku == sku).first()
    expected = item.quantity if item else 0
    variance = int(counted) - int(expected)
    if variance == 0:
        return {"outcome": "in_sync", "sku": sku, "expected": expected, "counted": int(counted)}

    rec = create_recommendation(
        db,
        kind="RECONCILIATION",
        subject_type="sku",
        subject_id=sku,
        summary=f"Inventory variance for {sku}: counted {counted} vs projected {expected} ({variance:+d})",
        rationale=f"observed via {event.source_system}/{event.source_device_id} seq {event.source_seq} (event {event.event_id})",
        confidence=0.9,
        recommended_action=json.dumps({"sku": sku, "counted_quantity": int(counted), "expected_quantity": int(expected), "variance": variance}),
        created_by="edge-reconciliation",
    )
    return {"outcome": "variance_detected", "sku": sku, "expected": expected, "counted": int(counted), "variance": variance, "recommendation": rec.uid}


# --------------------------------------------------------------- outbox

@router.get("/outbox/deltas")
def outbox_deltas(limit: int = Query(500, ge=1, le=5000), db: Session = Depends(get_db)):
    """Classifiable-replay export for the sync-go sidecar.

    Derives inventory deltas from PENDING/FAILED outbox events. Python owns the
    semantics (it understands POS receipts, counts, transfers, waste); the Go
    worker only classifies commutation and never sees raw payloads.

    Output JSONL-compatible list of {event_id, event_type, sku, delta, kind}.
    Unclassifiable event types are counted and skipped, never guessed.
    """
    events = (
        db.query(EdgeOutbox, EdgeEvent)
        .join(EdgeEvent, EdgeEvent.event_id == EdgeOutbox.event_id)
        .filter(EdgeOutbox.status.in_(["PENDING", "FAILED"]))
        .order_by(EdgeOutbox.id.asc())
        .limit(limit)
        .all()
    )
    deltas = []
    skipped = 0
    for outbox, event in events:
        if event.event_type != "pos.transaction.completed":
            skipped += 1
            continue
        try:
            payload = json.loads(event.payload)
        except ValueError:
            skipped += 1
            continue
        lines = payload.get("lines") if isinstance(payload, dict) else None
        if not isinstance(lines, list):
            skipped += 1
            continue
        for line in lines:
            sku = line.get("sku")
            qty = line.get("quantity")
            if sku and qty:
                deltas.append({
                    "event_id": event.event_id,
                    "event_type": event.event_type,
                    "sku": sku,
                    "delta": -int(qty),
                    "kind": "SALE",
                })
            else:
                skipped += 1
    checkpoint = None
    from app.edge.models import EdgeCheckpoint

    cp = db.query(EdgeCheckpoint).filter(EdgeCheckpoint.watermark_name == "outbox").first()
    if cp is not None:
        checkpoint = {"last_synced_event_id": cp.last_synced_event_id, "last_seq": cp.last_seq}
    return {"deltas": deltas, "count": len(deltas), "skipped_unclassified": skipped, "watermark": checkpoint}


@router.get("/outbox")
def outbox_status(db: Session = Depends(get_db)):
    pending = db.query(EdgeOutbox).filter(EdgeOutbox.status == "PENDING").count()
    failed = db.query(EdgeOutbox).filter(EdgeOutbox.status == "FAILED").count()
    synced = db.query(EdgeOutbox).filter(EdgeOutbox.status == "SYNCED").count()
    quarantined = db.query(EdgeOutbox).filter(EdgeOutbox.status == "QUARANTINED").count()
    oldest = db.query(EdgeOutbox).filter(EdgeOutbox.status == "PENDING").order_by(EdgeOutbox.id.asc()).first()
    return {
        "pending": pending,
        "failed": failed,
        "synced": synced,
        "quarantined": quarantined,
        "oldest_pending_event_id": oldest.event_id if oldest else None,
        "cloud_sync_configured": bool(settings.CLOUD_SYNC_URL),
    }


@router.post("/outbox/drain")
def outbox_drain(payload: DrainIn, db: Session = Depends(get_db)):
    """Attempt delivery of PENDING events to the configured cloud endpoint.

    Without CLOUD_SYNC_URL this is an honest dry-run: it reports what would be
    sent and changes nothing.
    """
    events = (
        db.query(EdgeOutbox)
        .filter(EdgeOutbox.status == "PENDING")
        .order_by(EdgeOutbox.id.asc())
        .limit(payload.max_batch)
        .all()
    )
    if not settings.CLOUD_SYNC_URL:
        return {
            "delivered": 0,
            "attempted": len(events),
            "reason": "CLOUD_SYNC_URL not configured; dry-run — nothing sent, nothing marked synced",
            "cloud_sync_configured": False,
        }

    delivered = failed = 0
    with httpx.Client(timeout=10.0) as client:
        for outbox in events:
            event = db.query(EdgeEvent).filter(EdgeEvent.event_id == outbox.event_id).first()
            try:
                response = client.post(
                    settings.CLOUD_SYNC_URL,
                    json={**event_to_dict(event), "payload": json.loads(event.payload)},
                    headers={"content-type": "application/json", "x-vejit-actor": payload.actor},
                )
                if 200 <= response.status_code < 300:
                    outbox.status = "SYNCED"
                    from datetime import datetime, timezone

                    outbox.synced_at = datetime.now(timezone.utc)
                    delivered += 1
                else:
                    outbox.status = "FAILED"
                    outbox.last_error = f"cloud returned {response.status_code}"
                    failed += 1
            except httpx.HTTPError as exc:
                outbox.status = "FAILED"
                outbox.last_error = str(exc)[:500]
                failed += 1
            outbox.attempts += 1
    db.commit()
    return {"delivered": delivered, "failed": failed, "attempted": len(events), "cloud_sync_configured": True}


@router.get("/checkpoints")
def list_checkpoints(db: Session = Depends(get_db)):
    from app.edge.models import EdgeCheckpoint

    rows = db.query(EdgeCheckpoint).order_by(EdgeCheckpoint.watermark_name.asc()).all()
    return {"checkpoints": [
        {"watermark_name": r.watermark_name, "last_synced_event_id": r.last_synced_event_id, "last_seq": r.last_seq}
        for r in rows
    ]}


# --------------------------------------------------------------- policy

@router.get("/policy/rules")
def list_policy_rules(active_only: bool = True, db: Session = Depends(get_db)):
    query = db.query(PolicyRule)
    if active_only:
        query = query.filter(PolicyRule.active.is_(True))
    return {"rules": [_rule_out(r) for r in query.order_by(PolicyRule.id.asc()).all()]}


@router.post("/policy/rules", status_code=201)
def create_policy_rule(payload: PolicyRuleIn, db: Session = Depends(get_db)):
    if payload.rule not in {"weight_mismatch", "quantity_threshold", "unknown_device", "stale_heartbeat", "unrecognized_event"}:
        raise HTTPException(status_code=422, detail=f"Unknown evaluator {payload.rule!r}")
    rule = PolicyRule(
        name=payload.name,
        rule=payload.rule,
        params=json.dumps(payload.params),
        requires_approval=payload.requires_approval,
        action_class=payload.action_class,
    )
    db.add(rule)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=409, detail=f"Policy rule {payload.name!r} already exists")
    return _rule_out(rule)


@router.post("/policy/seed-defaults")
def seed_default_policy_rules(payload: SeedPoliciesIn, db: Session = Depends(get_db)):
    return {"created": policy_engine.seed_default_policies(db)}


# --------------------------------------------------------------- skills

@router.get("/skills")
def skills_index():
    return {"skills": list_skills()}


@router.post("/skills/{name}/run")
def skill_run(name: str, payload: SkillRunIn, db: Session = Depends(get_db)):
    try:
        policy_engine.require_safety_gates(db)
    except policy_engine.PolicyGateClosed as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    try:
        return run_skill(name, payload.payload)
    except SkillError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)


# --------------------------------------------------------------- helpers

def _rule_out(r: PolicyRule) -> dict:
    try:
        params = json.loads(r.params)
    except ValueError:
        params = {}
    return {
        "id": r.id,
        "name": r.name,
        "rule": r.rule,
        "params": params,
        "requires_approval": r.requires_approval,
        "action_class": r.action_class,
        "policy_version": r.policy_version,
        "active": r.active,
    }


@router.get("/event-types")
def event_types():
    from app.edge.contracts import ENVELOPE_FIELD_GROUPS

    return {
        "schema_version": "vendarix.event.v1",
        "event_types": list(EVENT_TYPES),
        "envelope_field_groups": [list(g) for g in ENVELOPE_FIELD_GROUPS],
    }
