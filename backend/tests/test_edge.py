"""End-to-end tests for the Vendarix edge runtime.

Isolated SQLite via a get_db dependency override, unique SKUs/devices per run.
Tests run in definition order (state machine is per-database).
"""

import os
import tempfile
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("DATABASE_URL", "sqlite:///./vejit.db")

from app.db.database import Base, get_db
import app.planes.models  # noqa: F401
import app.edge.models  # noqa: F401
from app.main import app

_tmp = os.path.join(tempfile.gettempdir(), f"vejit_edge_test_{uuid.uuid4().hex}.db")
engine = create_engine(f"sqlite:///{_tmp}", connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db
Base.metadata.create_all(bind=engine)


import pytest as _pytest


@_pytest.fixture(autouse=True)
def _isolate_db():
    """Bind this module's test DB only while its tests run (imports are last-write-wins globally)."""
    app.dependency_overrides[get_db] = override_get_db
    from app.edge.policy import seed_default_policies

    seed_db = TestingSessionLocal()
    try:
        seed_default_policies(seed_db)
    finally:
        seed_db.close()
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_db, None)

client = TestClient(app)


def sfx():
    return uuid.uuid4().hex[:8].upper()


def heartbeat(device_id, vendor="ncr-voyix", interface="ncr-voyix/pos-v1", healthy=True):
    resp = client.post("/api/v1/edge/devices/heartbeat", json={
        "device_id": device_id, "vendor": vendor, "interface": interface, "healthy": healthy,
    })
    assert resp.status_code == 201, resp.text


def test_event_types_and_planes_registry():
    body = client.get("/api/v1/edge/event-types").json()
    assert "pos.transaction.completed" in body["event_types"]
    assert "edge.unrecognized" in body["event_types"]
    planes = client.get("/api/v1/planes").json()
    assert [p["id"] for p in planes["planes"]] == ["commerce", "operations", "intelligence"]


def test_seed_defaults_and_list_rules():
    resp = client.post("/api/v1/edge/policy/seed-defaults", json={"actor": "test-admin"})
    assert resp.status_code == 200
    rules = client.get("/api/v1/edge/policy/rules").json()["rules"]
    names = {r["name"] for r in rules}
    assert {"weight-mismatch-2pct", "quantity-threshold-50", "unknown-device-hold", "stale-heartbeat-hold", "unrecognized-hold"} <= names


def test_ncr_event_held_for_unknown_device_then_validated_after_heartbeat():
    device = f"ncr-{sfx()}"
    resp = client.post("/api/v1/edge/ingest", json={
        "vendor": "ncr-voyix", "device_id": device, "source_seq": 1,
        "raw": {"msgType": "TX_COMPLETE", "txnId": f"TXN-{sfx()}", "laneId": device,
                "lines": [{"sku": "DAIRY-001", "qty": 2, "price": 3.5}], "total": 7.0},
    })
    assert resp.status_code == 201, resp.text
    event = resp.json()["events"][0]
    assert event["outcome"] == "held"  # unknown device -> deterministic HOLD -> human required
    assert event["authorization"] == "HOLD"
    assert event["authorization_request"], "a human authorization request must be created"

    heartbeat(device)
    resp = client.post("/api/v1/edge/ingest", json={
        "vendor": "ncr-voyix", "device_id": device, "source_seq": 2,
        "raw": {"msgType": "TX_COMPLETE", "txnId": f"TXN-{sfx()}", "laneId": device,
                "lines": [{"sku": "DAIRY-001", "qty": 1, "price": 3.5}], "total": 3.5},
    })
    event = resp.json()["events"][0]
    assert event["outcome"] == "validated"
    assert event["route"] == "operations"
    assert event["authorization"] == "ALLOW"


def test_malformed_event_is_rejected_never_processed():
    device = f"ncr-{sfx()}"
    heartbeat(device)
    resp = client.post("/api/v1/edge/ingest", json={
        "vendor": "ncr-voyix", "device_id": device, "source_seq": 1,
        "raw": {"msgType": "TX_COMPLETE"},  # no txnId / lines -> adapter quarantines as unrecognized...
    })
    event = resp.json()["events"][0]
    # ...which the unrecognized-hold policy catches (requires approval, not blind processing)
    assert event["event_type"] == "edge.unrecognized"
    assert event["outcome"] == "held"


def test_contract_validation_rejects_bad_manifest():
    from app.edge.contracts import payload_hash, validate_manifest

    assert validate_manifest({"event_type": "pos.transaction.completed"})["ok"] is False
    assert validate_manifest({})["ok"] is False
    good = {
        "tenant_id": "t", "store_id": "s", "source_system": "x", "source_device_id": "d",
        "event_id": "e", "idempotency_key": "k", "event_type": "pos.transaction.completed",
        "schema_version": "vendarix.event.v1", "source_seq": 1,
        "occurred_at": "2026-11-17T00:00:00+00:00",
        "correlation_id": "c", "causation_id": None,
        "payload": {},
        "payload_hash": payload_hash({}),
        "auth_metadata": {},
    }
    assert validate_manifest(good)["ok"] is True
    bad = dict(good, payload_hash="0" * 64)
    result = validate_manifest(bad)
    assert result["ok"] is False
    assert any("payload_hash" in e for e in result["errors"])


def test_duplicate_idempotency_key_replays_without_reprocessing():
    device = f"ncr-{sfx()}"
    heartbeat(device)
    raw = {"msgType": "TX_COMPLETE", "txnId": f"TXN-{sfx()}", "laneId": device,
           "lines": [{"sku": "DAIRY-001", "qty": 3, "price": 2.0}], "total": 6.0}
    first = client.post("/api/v1/edge/ingest", json={"vendor": "ncr-voyix", "device_id": device, "source_seq": 7, "raw": raw}).json()["events"][0]
    second = client.post("/api/v1/edge/ingest", json={"vendor": "ncr-voyix", "device_id": device, "source_seq": 8, "raw": raw}).json()["events"][0]
    assert first["outcome"] == "validated"
    assert second["outcome"] == "duplicate"
    assert second["event_id"] == first["event_id"]


def test_inventory_count_reconciliation_flow():
    sku = f"EDGE-RECON-{sfx()}"
    device = f"ent-{sfx()}"
    heartbeat(device, vendor="enterprise", interface="enterprise/erp-wms-supplier-v1")
    client.post("/api/v1/inventory", json={"sku": sku, "name": sku, "quantity": 45, "reorder_point": 5, "unit_cost": 1.0})

    resp = client.post("/api/v1/edge/ingest", json={
        "vendor": "enterprise", "device_id": device, "source_seq": 42,
        "raw": {"event_type": "inventory.count.observed", "payload": {"sku": sku, "quantity": 40}},
    })
    event = resp.json()["events"][0]
    assert event["outcome"] == "validated", event

    recon = client.post(f"/api/v1/edge/events/{event['event_id']}/reconcile", json={"actor": "ops-1"}).json()
    assert recon["outcome"] == "variance_detected"
    assert recon["variance"] == -5

    uid = recon["recommendation"]
    client.post(f"/api/v1/intelligence/recommendations/{uid}/approve", json={"approver": "manager-1"})
    executed = client.post(f"/api/v1/operations/recommendations/{uid}/execute", json={"actor": "ops-1"})
    assert executed.status_code == 200, executed.text
    assert executed.json()["detail"]["cycle_count_applied"] is True
    assert executed.json()["detail"]["variance"] == -5

    item = [i for i in client.get("/api/v1/inventory").json() if i["sku"] == sku][0]
    assert item["quantity"] == 40
    ledger = client.get(f"/api/v1/operations/ledger?sku={sku}&reason=CYCLE_COUNT").json()["entries"]
    assert any(e["delta"] == -5 for e in ledger)


def test_skill_run_shelf_intelligence_and_listing():
    skills = client.get("/api/v1/edge/skills").json()["skills"]
    assert any(s["name"] == "shelf-intelligence" and s["approved"] for s in skills)

    resp = client.post("/api/v1/edge/skills/shelf-intelligence/run", json={
        "actor": "analyst-1",
        "payload": {"items": [{"sku": "DAIRY-001", "on_hand": 2, "reorder_point": 10, "daily_velocity": 4.0}]},
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["output"]["bands"]["critical"] + body["output"]["bands"]["at_risk"] + body["output"]["bands"]["stockout"] >= 1


def test_outbox_drain_is_honest_dry_run_without_cloud_url():
    resp = client.post("/api/v1/edge/outbox/drain", json={"actor": "ops-1", "max_batch": 5})
    assert resp.status_code == 200
    body = resp.json()
    assert body["delivered"] == 0
    assert body["cloud_sync_configured"] is False
    assert "dry-run" in body["reason"]


def test_continuity_status_reports_components():
    body = client.get("/api/v1/edge/status").json()
    cont = body["continuity"]
    assert cont["state"] == "ONLINE"
    assert 0.0 <= cont["value"] <= 100.0
    assert "database_integrity" in cont["components"]
    assert cont["hard_rules"].startswith("Continuity value never authorizes")


def test_safe_mode_blocks_actions_and_recovery_restores_online():
    resp = client.post("/api/v1/edge/continuity/transition", json={"to_state": "SAFE_MODE", "actor": "safety-officer-1", "reason": "integrity check failed"})
    assert resp.status_code == 200 and resp.json()["to_state"] == "SAFE_MODE"

    # Hard safety: business actions blocked regardless of continuity value
    sku = f"EDGE-SM-{sfx()}"
    client.post("/api/v1/inventory", json={"sku": sku, "name": sku, "quantity": 10, "reorder_point": 1, "unit_cost": 1.0})
    assert client.post("/api/v1/operations/adjustments", json={"sku": sku, "quantity_delta": -1, "actor": "ops-1"}).status_code == 409
    assert client.post("/api/v1/edge/skills/shelf-intelligence/run", json={"actor": "a", "payload": {"items": []}}).status_code == 409

    # Jumping straight back to ONLINE is illegal; recovery is explicit
    assert client.post("/api/v1/edge/continuity/transition", json={"to_state": "ONLINE", "actor": "ops-1", "reason": "looks fine"}).status_code == 409

    assert client.post("/api/v1/edge/continuity/transition", json={"to_state": "RECOVERING", "actor": "ops-1", "reason": "starting recovery"}).status_code == 200

    # Reconciliation verifies outbox hashes, replays validation, resolves conflicts, acks cloud
    recon = client.post("/api/v1/edge/recovery/reconcile", json={"actor": "ops-1", "resolution": "accept_local"})
    assert recon.status_code == 200, recon.text
    assert recon.json()["transition"]["to_state"] == "ONLINE"

    # Executions allowed again
    assert client.post("/api/v1/operations/adjustments", json={"sku": sku, "quantity_delta": -1, "actor": "ops-1"}).status_code == 201
