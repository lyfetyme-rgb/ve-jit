"""Cloud reconciliation tables — ingestion, idempotency, HITL quarantine.

Six-domain separation note: these tables live beside the edge tables in the
single embedded SQLite file for the pilot and local development. Managed
deployments move them into their own PostgreSQL schema with INSERT-only
grants where applicable (see infrastructure/database/cloud_reconciliation.sql).
"""

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.sql import func

from app.db.database import Base


def utcnow():
    return datetime.now(timezone.utc)


class CloudStoreWatermark(Base):
    """Per-store last verified watermark and hash-chain tip."""

    __tablename__ = "cloud_store_watermarks"
    __table_args__ = (UniqueConstraint("tenant_id", "store_id", name="uq_cloud_store_watermark"),)

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(String(64), nullable=False, index=True)
    store_id = Column(String(64), nullable=False, index=True)
    last_acked_seq = Column(Integer, nullable=False, default=0)
    last_verified_audit_hash = Column(String(128), nullable=False, default="GENESIS")
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class CloudIngestedEvent(Base):
    """Idempotency ledger: event IDs already applied (never double-apply)."""

    __tablename__ = "cloud_ingested_events"
    __table_args__ = (UniqueConstraint("tenant_id", "store_id", "event_id", name="uq_cloud_ingested_event"),)

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(String(64), nullable=False, index=True)
    store_id = Column(String(64), nullable=False, index=True)
    event_id = Column(String(64), nullable=False, index=True)
    recovery_session_id = Column(String(128), nullable=False)
    source_seq = Column(Integer, nullable=False, default=0)
    payload_hash = Column(String(64), nullable=False)
    ingested_at = Column(DateTime(timezone=True), server_default=func.now())


class CloudReconciliationQuarantine(Base):
    """HITL queue for batches the deterministic rules refuse to apply."""

    __tablename__ = "cloud_reconciliation_quarantine"

    id = Column(Integer, primary_key=True, index=True)
    quarantine_id = Column(String(64), unique=True, nullable=False, index=True)
    tenant_id = Column(String(64), nullable=False, index=True)
    store_id = Column(String(64), nullable=False, index=True)
    recovery_session_id = Column(String(128), nullable=True)
    source_event_id = Column(String(64), nullable=False, index=True)
    verdict = Column(String(32), nullable=False, default="quarantine")
    reason = Column(Text, nullable=False)
    payload = Column(Text, nullable=False)  # canonical JSON of the delta/event
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    resolved_by = Column(String(255), nullable=True)
    resolution = Column(String(32), nullable=True)  # apply|discard


class CloudInventory(Base):
    """Cloud-side inventory projection used by bulk replay. Compare-and-swap on version."""

    __tablename__ = "cloud_inventory"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(String(64), nullable=False, index=True)
    store_id = Column(String(64), nullable=False, index=True)
    sku = Column(String(100), nullable=False, index=True)
    quantity = Column(Integer, nullable=False, default=0)
    version = Column(Integer, nullable=False, server_default="1", default=1)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
