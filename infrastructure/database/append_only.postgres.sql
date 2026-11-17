-- Six-domain storage separation — PostgreSQL triggers (managed deployments).
-- Apply with: psql "$DATABASE_URL" -f infrastructure/database/append_only.postgres.sql
-- Edge runtime default uses infrastructure/database/append_only.sql (SQLite).

CREATE OR REPLACE FUNCTION edge_reject_mutation() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION '% is an append-only table; UPDATE/DELETE forbidden', TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS edge_events_no_update ON edge_events;
DROP TRIGGER IF EXISTS edge_events_no_delete ON edge_events;
CREATE TRIGGER edge_events_no_update BEFORE UPDATE ON edge_events FOR EACH ROW EXECUTE FUNCTION edge_reject_mutation();
CREATE TRIGGER edge_events_no_delete BEFORE DELETE ON edge_events FOR EACH ROW EXECUTE FUNCTION edge_reject_mutation();

DROP TRIGGER IF EXISTS inventory_ledger_no_update ON inventory_ledger;
DROP TRIGGER IF EXISTS inventory_ledger_no_delete ON inventory_ledger;
CREATE TRIGGER inventory_ledger_no_update BEFORE UPDATE ON inventory_ledger FOR EACH ROW EXECUTE FUNCTION edge_reject_mutation();
CREATE TRIGGER inventory_ledger_no_delete BEFORE DELETE ON inventory_ledger FOR EACH ROW EXECUTE FUNCTION edge_reject_mutation();

DROP TRIGGER IF EXISTS edge_continuity_records_no_update ON edge_continuity_records;
DROP TRIGGER IF EXISTS edge_continuity_records_no_delete ON edge_continuity_records;
CREATE TRIGGER edge_continuity_records_no_update BEFORE UPDATE ON edge_continuity_records FOR EACH ROW EXECUTE FUNCTION edge_reject_mutation();
CREATE TRIGGER edge_continuity_records_no_delete BEFORE DELETE ON edge_continuity_records FOR EACH ROW EXECUTE FUNCTION edge_reject_mutation();

DROP TRIGGER IF EXISTS waste_records_no_update ON waste_records;
DROP TRIGGER IF EXISTS waste_records_no_delete ON waste_records;
CREATE TRIGGER waste_records_no_update BEFORE UPDATE ON waste_records FOR EACH ROW EXECUTE FUNCTION edge_reject_mutation();
CREATE TRIGGER waste_records_no_delete BEFORE DELETE ON waste_records FOR EACH ROW EXECUTE FUNCTION edge_reject_mutation();

DROP TRIGGER IF EXISTS vendor_messages_no_update ON vendor_messages;
DROP TRIGGER IF EXISTS vendor_messages_no_delete ON vendor_messages;
CREATE TRIGGER vendor_messages_no_update BEFORE UPDATE ON vendor_messages FOR EACH ROW EXECUTE FUNCTION edge_reject_mutation();
CREATE TRIGGER vendor_messages_no_delete BEFORE DELETE ON vendor_messages FOR EACH ROW EXECUTE FUNCTION edge_reject_mutation();
