-- 0001: append-only (INSERT-only) triggers for the event store and audit ledger.
-- Mirrors infrastructure/database/append_only.sql at migration time; kept here so a
-- fresh database converges through migrate.py alone.
-- Pairs with ORM-level enforcement in backend/app/edge/integrity.py.

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
