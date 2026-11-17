-- Defense in depth for the six-domain storage separation.
-- ORM-level enforcement lives in backend/app/edge/integrity.py (before_update/before_delete
-- raising AppendOnlyViolation). These triggers enforce the same invariant at the database,
-- so no code path — ORM, raw SQL or migration gone wrong — can mutate or delete the
-- event store or the audit ledger.
--
-- Event store:    edge_events
-- Audit ledger:   inventory_ledger, edge_continuity_records, waste_records, vendor_messages
-- (edge_outbox is exempt BY DESIGN: it is the cloud-synchronization cursor, not the audit trail.)

-- ------------------------------- PostgreSQL
CREATE OR REPLACE FUNCTION edge_reject_mutation() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION '% is an append-only table; UPDATE/DELETE forbidden', TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS edge_events_no_update ON edge_events;
DROP TRIGGER IF EXISTS inventory_ledger_no_update ON inventory_ledger;
DROP TRIGGER IF EXISTS edge_continuity_records_no_update ON edge_continuity_records;
DROP TRIGGER IF EXISTS waste_records_no_update ON waste_records;
DROP TRIGGER IF EXISTS vendor_messages_no_update ON vendor_messages;

DROP TRIGGER IF EXISTS edge_events_no_delete ON edge_events;

CREATE TRIGGER edge_events_no_update BEFORE UPDATE ON edge_events FOR EACH ROW EXECUTE FUNCTION edge_reject_mutation();
CREATE TRIGGER edge_events_no_delete BEFORE DELETE ON edge_events FOR EACH ROW EXECUTE FUNCTION edge_reject_mutation();
CREATE TRIGGER inventory_ledger_no_update BEFORE UPDATE ON inventory_ledger FOR EACH ROW EXECUTE FUNCTION edge_reject_mutation();
CREATE TRIGGER inventory_ledger_no_delete BEFORE DELETE ON inventory_ledger FOR EACH ROW EXECUTE FUNCTION edge_reject_mutation();
CREATE TRIGGER edge_continuity_records_no_update BEFORE UPDATE ON edge_continuity_records FOR EACH ROW EXECUTE FUNCTION edge_reject_mutation();
CREATE TRIGGER edge_continuity_records_no_delete BEFORE DELETE ON edge_continuity_records FOR EACH ROW EXECUTE FUNCTION edge_reject_mutation();
CREATE TRIGGER waste_records_no_update BEFORE UPDATE ON waste_records FOR EACH ROW EXECUTE FUNCTION edge_reject_mutation();
CREATE TRIGGER waste_records_no_delete BEFORE DELETE ON waste_records FOR EACH ROW EXECUTE FUNCTION edge_reject_mutation();
CREATE TRIGGER vendor_messages_no_update BEFORE UPDATE ON vendor_messages FOR EACH ROW EXECUTE FUNCTION edge_reject_mutation();
CREATE TRIGGER vendor_messages_no_delete BEFORE DELETE ON vendor_messages FOR EACH ROW EXECUTE FUNCTION edge_reject_mutation();

-- ------------------------------- SQLite (via sqlite3 CLI / migration runner)
-- CREATE TRIGGER IF NOT EXISTS edge_events_no_update BEFORE UPDATE ON edge_events
--   BEGIN SELECT RAISE(ABORT, 'edge_events is append-only'); END;
-- CREATE TRIGGER IF NOT EXISTS edge_events_no_delete BEFORE DELETE ON edge_events
--   BEGIN SELECT RAISE(ABORT, 'edge_events is append-only'); END;
-- CREATE TRIGGER IF NOT EXISTS inventory_ledger_no_update BEFORE UPDATE ON inventory_ledger
--   BEGIN SELECT RAISE(ABORT, 'inventory_ledger is append-only'); END;
-- CREATE TRIGGER IF NOT EXISTS inventory_ledger_no_delete BEFORE DELETE ON inventory_ledger
--   BEGIN SELECT RAISE(ABORT, 'inventory_ledger is append-only'); END;
-- CREATE TRIGGER IF NOT EXISTS edge_continuity_records_no_update BEFORE UPDATE ON edge_continuity_records
--   BEGIN SELECT RAISE(ABORT, 'edge_continuity_records is append-only'); END;
-- CREATE TRIGGER IF NOT EXISTS edge_continuity_records_no_delete BEFORE DELETE ON edge_continuity_records
--   BEGIN SELECT RAISE(ABORT, 'edge_continuity_records is append-only'); END;
-- CREATE TRIGGER IF NOT EXISTS waste_records_no_update BEFORE UPDATE ON waste_records
--   BEGIN SELECT RAISE(ABORT, 'waste_records is append-only'); END;
-- CREATE TRIGGER IF NOT EXISTS waste_records_no_delete BEFORE DELETE ON waste_records
--   BEGIN SELECT RAISE(ABORT, 'waste_records is append-only'); END;
-- CREATE TRIGGER IF NOT EXISTS vendor_messages_no_update BEFORE UPDATE ON vendor_messages
--   BEGIN SELECT RAISE(ABORT, 'vendor_messages is append-only'); END;
-- CREATE TRIGGER IF NOT EXISTS vendor_messages_no_delete BEFORE DELETE ON vendor_messages
--   BEGIN SELECT RAISE(ABORT, 'vendor_messages is append-only'); END;
