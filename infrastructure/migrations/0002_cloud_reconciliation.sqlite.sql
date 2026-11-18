-- 0002: cloud reconciliation tables — watermarks, idempotency ledger, HITL quarantine,
-- and the cloud-side inventory projection. Mirrors backend/app/cloud/models.py.
-- Engine: SQLite (production default). Names kept identical to the ORM for convergence.

CREATE TABLE IF NOT EXISTS cloud_store_watermarks (
    id INTEGER PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    store_id TEXT NOT NULL,
    last_acked_seq INTEGER NOT NULL DEFAULT 0,
    last_verified_audit_hash TEXT NOT NULL DEFAULT 'GENESIS',
    updated_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_cloud_store_watermark ON cloud_store_watermarks (tenant_id, store_id);

CREATE TABLE IF NOT EXISTS cloud_ingested_events (
    id INTEGER PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    store_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    recovery_session_id TEXT NOT NULL,
    source_seq INTEGER NOT NULL DEFAULT 0,
    payload_hash TEXT NOT NULL,
    ingested_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_cloud_ingested_event ON cloud_ingested_events (tenant_id, store_id, event_id);
CREATE INDEX IF NOT EXISTS ix_ingested_session ON cloud_ingested_events (recovery_session_id);

CREATE TABLE IF NOT EXISTS cloud_reconciliation_quarantine (
    id INTEGER PRIMARY KEY,
    quarantine_id TEXT NOT NULL UNIQUE,
    tenant_id TEXT NOT NULL,
    store_id TEXT NOT NULL,
    recovery_session_id TEXT,
    sku TEXT,
    source_event_ids TEXT NOT NULL DEFAULT '[]',
    verdict TEXT NOT NULL DEFAULT 'quarantine',
    reason TEXT NOT NULL,
    edge_delta_qty INTEGER NOT NULL DEFAULT 0,
    baseline_cloud_qty INTEGER,
    current_cloud_qty INTEGER,
    status TEXT NOT NULL DEFAULT 'PENDING_REVIEW',
    resolution_note TEXT,
    payload TEXT NOT NULL,
    created_at TEXT,
    reviewed_at TEXT,
    reviewed_by TEXT,
    resolution TEXT
);
CREATE INDEX IF NOT EXISTS ix_quarantine_pending ON cloud_reconciliation_quarantine (tenant_id, store_id) WHERE status = 'PENDING_REVIEW';

CREATE TABLE IF NOT EXISTS cloud_store_inventory (
    id INTEGER PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    store_id TEXT NOT NULL,
    sku TEXT NOT NULL,
    on_hand_qty INTEGER NOT NULL DEFAULT 0,
    version INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_cloud_store_inventory ON cloud_store_inventory (tenant_id, store_id, sku);
