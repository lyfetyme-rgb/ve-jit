"""Vendarix edge contracts.

One event manifest for every hardware / POS / ERP / WMS / supplier event
entering the edge node. Adapters translate proprietary device payloads into
this manifest; the core never parses vendor-specific payloads.

Every event carries: tenant and store identity, source system and device
identity, a unique event id and idempotency key, event type and schema
version, source sequence and timestamps, correlation and causation ids, a
payload hash, and processing and authorization metadata.
"""

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

SCHEMA_VERSION = "vendarix.event.v1"

EVENT_TYPES = (
    # POS / terminal
    "pos.transaction.completed",
    "pos.price_lookup",
    "pos.void.observed",
    # scanner-scale / weighing
    "scanner.weight.measured",
    "scanner.item.scanned",
    "scale.calibrated",
    "scale.label.printed",
    # inventory / receiving
    "inventory.count.observed",
    "inventory.receipt.verified",
    # enterprise systems
    "erp.order.changed",
    "wms.transfer.observed",
    "supplier.event.received",
    # edge internal
    "edge.device.heartbeat",
    "edge.unrecognized",
)

ROUTES = {
    "pos.transaction.completed": "operations",
    "pos.price_lookup": "intelligence",
    "pos.void.observed": "operations",
    "scanner.weight.measured": "operations",
    "scanner.item.scanned": "operations",
    "scale.calibrated": "operations",
    "scale.label.printed": "operations",
    "inventory.count.observed": "operations",
    "inventory.receipt.verified": "operations",
    "erp.order.changed": "operations",
    "wms.transfer.observed": "operations",
    "supplier.event.received": "intelligence",
    "edge.device.heartbeat": "operations",
    "edge.unrecognized": "operations",
}

REQUIRED_FIELDS = (
    "tenant_id",
    "store_id",
    "source_system",
    "device_id",
    "event_id",
    "idempotency_key",
    "event_type",
    "schema_version",
    "source_seq",
    "occurred_at",
)


class ContractError(Exception):
    """Raised when an event fails contract validation."""

    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def payload_hash(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def new_event_id() -> str:
    return f"evt_{uuid.uuid4().hex[:16]}"


def build_manifest(
    *,
    event_type: str,
    tenant_id: str,
    store_id: str,
    source_system: str,
    device_id: str,
    payload: dict,
    source_seq: int,
    occurred_at: Optional[datetime] = None,
    event_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    correlation_id: Optional[str] = None,
    causation_id: Optional[str] = None,
    authorization_metadata: Optional[dict] = None,
) -> dict:
    now = datetime.now(timezone.utc)
    event_id = event_id or new_event_id()
    key = idempotency_key or f"{source_system}:{device_id}:{event_id}"
    body = dict(payload)
    return {
        "tenant_id": tenant_id,
        "store_id": store_id,
        "source_system": source_system,
        "device_id": device_id,
        "event_id": event_id,
        "idempotency_key": key,
        "event_type": event_type,
        "schema_version": SCHEMA_VERSION,
        "source_seq": int(source_seq),
        "occurred_at": (occurred_at or now).isoformat(),
        "received_at": now.isoformat(),
        "correlation_id": correlation_id or f"corr_{uuid.uuid4().hex[:16]}",
        "causation_id": causation_id,
        "payload_hash": payload_hash(body),
        "payload": body,
        "authorization_metadata": authorization_metadata or {},
        "processing_metadata": {},
    }


def validate_manifest(manifest: Any) -> dict:
    """Validate a manifest. Returns {"ok": True} or {"ok": False, "errors": [...]}."""
    errors: list[str] = []
    if not isinstance(manifest, dict):
        return {"ok": False, "errors": ["event must be a JSON object"]}

    for field in REQUIRED_FIELDS:
        value = manifest.get(field)
        if value is None or (isinstance(value, str) and not value.strip()):
            errors.append(f"missing required field: {field}")

    event_type = manifest.get("event_type")
    if event_type and event_type not in EVENT_TYPES:
        errors.append(f"unsupported event_type: {event_type!r}")

    version = manifest.get("schema_version")
    if version and version != SCHEMA_VERSION:
        errors.append(f"unsupported schema_version: {version!r}; expected {SCHEMA_VERSION}")

    source_seq = manifest.get("source_seq")
    if source_seq is not None and not isinstance(source_seq, int):
        errors.append("source_seq must be an integer")

    for stamp_field in ("occurred_at", "received_at"):
        value = manifest.get(stamp_field)
        if value is not None:
            try:
                datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            except ValueError:
                errors.append(f"{stamp_field} is not a valid ISO-8601 timestamp")

    payload = manifest.get("payload")
    if payload is not None and not isinstance(payload, dict):
        errors.append("payload must be a JSON object")

    expected_hash = manifest.get("payload_hash")
    if expected_hash is not None and isinstance(payload, dict):
        actual = payload_hash(payload)
        if actual != expected_hash:
            errors.append("payload_hash does not match payload (event integrity failure)")

    route = ROUTES.get(event_type or "")
    if event_type in EVENT_TYPES and route is None:
        errors.append(f"no route for event_type {event_type!r}")

    if errors:
        return {"ok": False, "errors": errors}
    return {"ok": True}


def route_for(event_type: str) -> Optional[str]:
    return ROUTES.get(event_type)
