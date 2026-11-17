-- Six-domain storage separation — SQLite triggers (production default).
-- ORM-level enforcement lives in backend/app/edge/integrity.py. These triggers enforce
-- the same invariant when the database is opened by non-ORM code (sqlite3 CLI, migrations).
--
-- PostgreSQL variant: infrastructure/database/append_only.postgres.sql
--
-- Event store:    edge_events
-- Audit ledger:   inventory_ledger, edge_continuity_records, waste_records, vendor_messages
-- (edge_outbox is exempt BY DESIGN: it is the cloud-synchronization cursor.)

CREATE TRIGGER IF NOT EXISTS edge_events_no_update BEFORE UPDATE ON edge_events
  BEGIN SELECT RAISE(ABORT, 'edge_events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS edge_events_no_delete BEFORE DELETE ON edge_events
  BEGIN SELECT RAISE(ABORT, 'edge_events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS inventory_ledger_no_update BEFORE UPDATE ON inventory_ledger
  BEGIN SELECT RAISE(ABORT, 'inventory_ledger is append-only'); END;
CREATE TRIGGER IF NOT EXISTS inventory_ledger_no_delete BEFORE DELETE ON inventory_ledger
  BEGIN SELECT RAISE(ABORT, 'inventory_ledger is append-only'); END;
CREATE TRIGGER IF NOT EXISTS edge_continuity_records_no_update BEFORE UPDATE ON edge_continuity_records
  BEGIN SELECT RAISE(ABORT, 'edge_continuity_records is append-only'); END;
CREATE TRIGGER IF NOT EXISTS edge_continuity_records_no_delete BEFORE DELETE ON edge_continuity_records
  BEGIN SELECT RAISE(ABORT, 'edge_continuity_records is append-only'); END;
CREATE TRIGGER IF NOT EXISTS waste_records_no_update BEFORE UPDATE ON waste_records
  BEGIN SELECT RAISE(ABORT, 'waste_records is append-only'); END;
CREATE TRIGGER IF NOT EXISTS waste_records_no_delete BEFORE DELETE ON waste_records
  BEGIN SELECT RAISE(ABORT, 'waste_records is append-only'); END;
CREATE TRIGGER IF NOT EXISTS vendor_messages_no_update BEFORE UPDATE ON vendor_messages
  BEGIN SELECT RAISE(ABORT, 'vendor_messages is append-only'); END;
CREATE TRIGGER IF NOT EXISTS vendor_messages_no_delete BEFORE DELETE ON vendor_messages
  BEGIN SELECT RAISE(ABORT, 'vendor_messages is append-only'); END;
