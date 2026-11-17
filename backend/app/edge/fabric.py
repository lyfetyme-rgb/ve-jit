"""The event fabric: the communication backbone between the three planes.

Pipeline (every stage enforced, in order):

  HARDWARE / POS / ERP / WMS / SUPPLIERS
      -> INGESTION (adapter translates to the Vendarix manifest)
      -> SCHEMA VALIDATION (contracts.validate_manifest)
      -> NORMALIZATION (already normalized by the adapter; hash re-verified)
      -> IDENTITY CHECK (tenant/store/device present + device healthy)
      -> EVENT ROUTING (operations | intelligence)
      -> POLICY (deterministic; HOLD creates a human authorization request)
      -> AUTHORIZATION (human via the recommendation approval flow)
      -> EVENT STORE + TRANSACTIONAL OUTBOX (one transaction)

Invalid events are never processed. They are stored with status REJECTED and
the validation errors, because silent drops are how shelf-inventory drift
happens. Duplicate idempotency keys replay to the original event without
re-processing.
"""

import json
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.edge.contracts import payload_hash, route_for, validate_manifest
from app.edge.models import AdapterHeartbeat, EdgeEvent, EdgeOutbox
from app.edge import policy as policy_engine
from app.planes.governance import create_recommendation


class IdentityError(Exception):
    pass


def ingest(
    db: Session,
    *,
    manifest: dict,
    raw_source: str,
    action_class: str = "general",
) -> dict:
    """Run one normalized manifest through the fabric. Never raises for bad data."""

    # -- SCHEMA VALIDATION ---------------------------------------------------
    validation = validate_manifest(manifest)
    if not validation["ok"]:
        record = EdgeEvent(
            event_id=manifest.get("event_id", f"rejected_{manifest.get('source_seq', 'na')}"),
            idempotency_key=manifest.get("idempotency_key", f"rejected:{raw_source}:{id(manifest)}"),
            tenant_id=str(manifest.get("tenant_id", "unknown"))[:64] or "unknown",
            store_id=str(manifest.get("store_id", "unknown"))[:64] or "unknown",
            source_system=str(manifest.get("source_system", raw_source))[:64] or raw_source,
            source_device_id=str(manifest.get("source_device_id", "unknown"))[:128] or "unknown",
            event_type=str(manifest.get("event_type", "unknown"))[:64] or "unknown",
            schema_version=str(manifest.get("schema_version", "unknown"))[:32] or "unknown",
            source_seq=int(manifest.get("source_seq") or 0),
            occurred_at=_parse_ts(manifest.get("occurred_at")) or datetime.now(timezone.utc),
            correlation_id=str(manifest.get("correlation_id", "unknown"))[:64] or "unknown",
            causation_id=manifest.get("causation_id"),
            payload_hash=str(manifest.get("payload_hash") or payload_hash(manifest.get("payload") or {})),
            payload=json.dumps(manifest.get("payload") or {}, sort_keys=True),
            route=None,
            status="REJECTED",
            processing_metadata=json.dumps({"errors": validation["errors"]}),
        )
        db.add(record)
        db.commit()
        return {"outcome": "rejected", "event_id": record.event_id, "event_type": record.event_type, "errors": validation["errors"]}

    # -- NORMALIZATION (hash re-verify happens inside validation) ------------
    payload = manifest["payload"]
    route = route_for(manifest["event_type"])

    # -- IDENTITY CHECK ------------------------------------------------------
    source_device_id = manifest["source_device_id"]
    heartbeat = db.query(AdapterHeartbeat).filter(AdapterHeartbeat.device_id == source_device_id).first()
    identity_ok = bool(manifest.get("tenant_id") and manifest.get("store_id") and source_device_id)
    identity_notes = []
    if not identity_ok:
        identity_notes.append("tenant/store/device identity incomplete")
    if heartbeat is not None and not heartbeat.healthy:
        identity_notes.append(f"device {source_device_id} reported unhealthy")

    # -- IDEMPOTENCY (unique constraints are the hard guard; this is the soft path) --
    existing = db.query(EdgeEvent).filter(EdgeEvent.idempotency_key == manifest["idempotency_key"]).first()
    if existing is not None:
        return {"outcome": "duplicate", "event_id": existing.event_id, "event_type": existing.event_type, "route": existing.route, "status": existing.status}

    # -- EVENT ROUTING + POLICY ----------------------------------------------
    decision = policy_engine.evaluate_event(db, manifest, action_class="general")

    tenant_store_ok = identity_ok and not any("unhealthy" in n for n in identity_notes)
    if decision["decision"] == "DENY":
        # Hard safety gate closed (SAFE_MODE / stale policy bundle / no credentials).
        # The event is recorded; it is never processed and never queued.
        status = "BLOCKED"
        authorization_metadata = {"authorization": "DENY", "reason": decision}
    elif not tenant_store_ok:
        status = "REJECTED"
        authorization_metadata = {"authorization": "DENY", "reason": identity_notes}
    elif decision["requires_approval"]:
        status = "HELD"
        authorization_metadata = {"authorization": "HOLD", "policy": decision}
    else:
        status = "VALIDATED"
        authorization_metadata = {"authorization": decision["decision"], "policy": decision}

    event = EdgeEvent(
        event_id=manifest["event_id"],
        idempotency_key=manifest["idempotency_key"],
        tenant_id=manifest["tenant_id"],
        store_id=manifest["store_id"],
        source_system=manifest["source_system"],
        source_device_id=source_device_id,
        event_type=manifest["event_type"],
        schema_version=manifest["schema_version"],
        source_seq=manifest["source_seq"],
        occurred_at=_parse_ts(manifest["occurred_at"]),
        received_at=_parse_ts(manifest.get("received_at")) or datetime.now(timezone.utc),
        correlation_id=manifest["correlation_id"],
        causation_id=manifest.get("causation_id"),
        payload_hash=manifest["payload_hash"],
        payload=json.dumps(payload, sort_keys=True),
        route=route,
        status=status,
        processing_metadata=json.dumps({"identity_notes": identity_notes, "route": route}),
        auth_metadata=json.dumps(authorization_metadata),
    )
    db.add(event)

    # -- AUTHORIZATION: place the hold on the human queue --------------------
    authorization_request_uid: Optional[str] = None
    if status == "HELD":
        rec = create_recommendation(
            db,
            kind="AUTHORIZATION_REQUEST",
            subject_type="edge_event",
            subject_id=event.event_id,
            summary=f"Authorization required: {event.event_type} from {event.source_device_id} ({decision['decision']})",
            rationale=json.dumps(decision),
            confidence=1.0,
            recommended_action=json.dumps({"event_id": event.event_id, "action_class": action_class}),
            created_by="edge-fabric",
        )
        authorization_request_uid = rec.uid

    # -- TRANSACTIONAL OUTBOX -------------------------------------------------
    if status in ("VALIDATED", "HELD"):
        db.add(EdgeOutbox(event_id=event.event_id, payload_hash=event.payload_hash, status="PENDING"))

    db.commit()

    return {
        "outcome": status.lower(),
        "event_id": event.event_id,
        "event_type": event.event_type,
        "route": route,
        "authorization": authorization_metadata["authorization"],
        "policy": decision,
        "authorization_request": authorization_request_uid,
    }


def event_to_dict(event: EdgeEvent) -> dict:
    def _parse(value):
        if value is None:
            return None
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return value

    return {
        "event_id": event.event_id,
        "idempotency_key": event.idempotency_key,
        "tenant_id": event.tenant_id,
        "store_id": event.store_id,
        "source_system": event.source_system,
        "source_device_id": event.source_device_id,
        "event_type": event.event_type,
        "schema_version": event.schema_version,
        "source_seq": event.source_seq,
        "occurred_at": event.occurred_at.isoformat() if event.occurred_at else None,
        "received_at": event.received_at.isoformat() if event.received_at else None,
        "correlation_id": event.correlation_id,
        "causation_id": event.causation_id,
        "payload_hash": event.payload_hash,
        "payload": _parse(event.payload),
        "route": event.route,
        "status": event.status,
        "processing_metadata": _parse(event.processing_metadata),
        "auth_metadata": _parse(event.auth_metadata),
    }


def _parse_ts(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
