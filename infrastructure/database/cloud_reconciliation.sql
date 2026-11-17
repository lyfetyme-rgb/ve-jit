-- Cloud replay & reconciliation (PostgreSQL reference).
-- Pilot/dev use the same tables through the shared embedded engine (app/cloud/models.py +
-- app/cloud/replay_router.py); managed deployments apply this migration into a dedicated schema.
--
-- Invariants enforced here as well as in application code:
--   1. Hash-chain continuity per store (watermark tip verification, FOR UPDATE row lock)
--   2. Batch idempotency & dedup (uq_cloud_ingested_event)
--   3. Quarantine-first conflict routing (HITL queue with machine reason + payload)

CREATE SCHEMA IF NOT EXISTS vendarix_cloud;
SET search_path TO vendarix_cloud;

-- Tracks each store's last verified watermark and hash-chain tip
CREATE TABLE IF NOT EXISTS cloud_store_watermarks (
    tenant_id TEXT NOT NULL,
    store_id TEXT NOT NULL,
    last_acked_seq BIGINT NOT NULL DEFAULT 0,
    last_verified_audit_hash TEXT NOT NULL DEFAULT 'GENESIS',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, store_id)
);

-- Deduplicates replayed recovery sessions and individual source event IDs
CREATE TABLE IF NOT EXISTS cloud_ingested_events (
    tenant_id TEXT NOT NULL,
    store_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    recovery_session_id TEXT NOT NULL,
    source_seq BIGINT NOT NULL DEFAULT 0,
    payload_hash TEXT NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, store_id, event_id)
);

-- Human-in-the-loop (HITL) conflict queue for quarantined sync-go batches
CREATE TABLE IF NOT EXISTS cloud_reconciliation_quarantine (
    quarantine_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    store_id TEXT NOT NULL,
    recovery_session_id TEXT,
    sku TEXT,
    source_event_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    verdict TEXT NOT NULL DEFAULT 'quarantine',
    conflict_reason TEXT NOT NULL,
    edge_delta_qty BIGINT NOT NULL DEFAULT 0,
    baseline_cloud_qty BIGINT,
    current_cloud_qty BIGINT,
    status TEXT NOT NULL DEFAULT 'PENDING_REVIEW',
    resolution_note TEXT,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    reviewed_at TIMESTAMPTZ,
    reviewed_by TEXT,
    resolution TEXT  -- APPROVED_APPLY | REJECTED_VOID
);

-- Cloud-side inventory projection used by bulk replay; compare-and-swap on version
CREATE TABLE IF NOT EXISTS cloud_store_inventory (
    tenant_id TEXT NOT NULL,
    store_id TEXT NOT NULL,
    sku TEXT NOT NULL,
    on_hand_qty BIGINT NOT NULL DEFAULT 0,
    version BIGINT NOT NULL DEFAULT 1,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, store_id, sku)
);

CREATE INDEX IF NOT EXISTS ix_quarantine_pending ON cloud_reconciliation_quarantine (tenant_id, store_id) WHERE status = 'PENDING_REVIEW';
CREATE INDEX IF NOT EXISTS ix_ingested_session ON cloud_ingested_events (recovery_session_id);
