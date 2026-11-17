"""Edge persistence.

Six logical persistence domains from the platform specification map onto the
schema as follows:

| Domain                | Implementation                                        |
|-----------------------|-------------------------------------------------------|
| Event store           | `edge_events` (immutable observed events)             |
| Operational database  | existing `inventory_items`, `purchase_orders`, ...    |
| Policy database       | `edge_policy_rules`                                   |
| Intelligence database | existing `recommendations`                            |
| Continuity database   | `edge_continuity_records`, `edge_continuity_scores`,  |
|                       | `edge_checkpoints`                                    |
| Audit ledger          | existing `recommendations` lifecycle + `inventory_ledger` + `edge_events.authorization_metadata` |

The outbox is transactional: an event is committed to the event store and to
`edge_outbox` in the same database transaction, so publication cannot be lost.
"""

from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Float, Integer, String, Text
from sqlalchemy.sql import func

from app.db.database import Base

EDGE_STATES = ("ONLINE", "DEGRADED", "OFFLINE", "SAFE_MODE", "RECOVERING")
OUTBOX_STATUSES = ("PENDING", "SYNCED", "FAILED", "QUARANTINED")


def utcnow():
    return datetime.now(timezone.utc)


class EdgeEvent(Base):
    """Immutable observed event (event store). Rows are never updated in place;
    processing state advances via dedicated columns and authorization metadata."""

    __tablename__ = "edge_events"

    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(String(64), unique=True, index=True, nullable=False)
    idempotency_key = Column(String(255), unique=True, nullable=False, index=True)
    tenant_id = Column(String(64), nullable=False, index=True)
    store_id = Column(String(64), nullable=False, index=True)
    source_system = Column(String(64), nullable=False)
    device_id = Column(String(128), nullable=False, index=True)
    event_type = Column(String(64), index=True, nullable=False)
    schema_version = Column(String(32), nullable=False)
    source_seq = Column(Integer, nullable=False)
    occurred_at = Column(DateTime(timezone=True), nullable=False)
    received_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    correlation_id = Column(String(64), index=True, nullable=False)
    causation_id = Column(String(64), nullable=True)
    payload_hash = Column(String(64), nullable=False)
    payload = Column(Text, nullable=False)  # canonical JSON
    route = Column(String(32), index=True, nullable=True)  # operations | intelligence
    status = Column(String(32), nullable=False, default="RECEIVED", index=True)  # RECEIVED|VALIDATED|REJECTED|PROCESSED| held statuses below
    processing_metadata = Column(Text, nullable=True)
    authorization_metadata = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class EdgeOutbox(Base):
    """Transactional outbox for cloud synchronization."""

    __tablename__ = "edge_outbox"

    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(String(64), unique=True, nullable=False, index=True)
    payload_hash = Column(String(64), nullable=False)
    status = Column(String(32), nullable=False, default="PENDING", index=True)
    attempts = Column(Integer, nullable=False, default=0)
    last_error = Column(Text, nullable=True)
    synced_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class EdgeCheckpoint(Base):
    """Replay checkpoints and watermarks (continuity database)."""

    __tablename__ = "edge_checkpoints"

    id = Column(Integer, primary_key=True, index=True)
    watermark_name = Column(String(64), unique=True, nullable=False)  # e.g. "outbox"
    last_synced_event_id = Column(String(64), nullable=True)
    last_seq = Column(Integer, nullable=False, default=0)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class PolicyRule(Base):
    """Policy database: business rules, thresholds and approval requirements.

    Deterministic evaluation; no model involved. `rule` is one of the known
    deterministic evaluators implemented in app/edge/policy.py.
    """

    __tablename__ = "edge_policy_rules"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(128), unique=True, nullable=False)
    rule = Column(String(64), nullable=False)  # weight_mismatch|quantity_threshold|unknown_device|stale_heartbeat|unrecognized_event
    params = Column(Text, nullable=False, default="{}")  # JSON
    requires_approval = Column(Boolean, nullable=False, default=False)
    action_class = Column(String(64), nullable=False, default="inventory")  # inventory|receiving|transfer|waste|general
    policy_version = Column(Integer, nullable=False, default=1)
    active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class ContinuityRecord(Base):
    """Continuity state machine history (append-only)."""

    __tablename__ = "edge_continuity_records"

    id = Column(Integer, primary_key=True, index=True)
    from_state = Column(String(32), nullable=False)
    to_state = Column(String(32), nullable=False)
    reason = Column(String(512), nullable=False)
    actor = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class ContinuityScore(Base):
    """Persisted continuity value snapshots (continuity database)."""

    __tablename__ = "edge_continuity_scores"

    id = Column(Integer, primary_key=True, index=True)
    state = Column(String(32), nullable=False)
    value = Column(Float, nullable=False)
    components = Column(Text, nullable=False)  # JSON breakdown
    computed_by = Column(String(255), nullable=False, default="continuity-engine")
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class AdapterHeartbeat(Base):
    """Device health for adapter inventory (feeds the continuity value)."""

    __tablename__ = "edge_adapter_heartbeats"

    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(String(128), unique=True, nullable=False, index=True)
    vendor = Column(String(64), nullable=False)
    interface = Column(String(64), nullable=False)
    healthy = Column(Boolean, nullable=False, default=True)
    detail = Column(Text, nullable=True)
    last_seen = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
