"""Cloud-side ingestion: hash-chain continuity, idempotency, quarantine-first."""

import json
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
import app.cloud.models  # noqa: F401
from app.main import app

_tmp = os.path.join(tempfile.gettempdir(), f"vejit_cloud_test_{uuid.uuid4().hex}.db")
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


@pytest.fixture(autouse=True)
def _isolate_db():
    app.dependency_overrides[get_db] = override_get_db
    yield
    app.dependency_overrides.pop(get_db, None)


client = TestClient(app)


def sfx():
    return uuid.uuid4().hex[:8].upper()


def seed_inventory(sku, quantity):
    resp = client.post("/api/v1/cloud/inventory/seed", json={"sku": sku, "quantity": quantity, "actor": "ops"})
    assert resp.status_code == 201, resp.text


def pos_event(sku, qty, seq, session_event_id=None, store="store_001", tenant="tenant_001", total=None):
    from app.edge.contracts import build_manifest

    payload = {
        "transaction_id": f"T-{sfx()}",
        "lane_id": f"lane-{sfx()}",
        "lines": [{"sku": sku, "quantity": qty, "unit_price": 2.5}],
        "total": total if total is not None else qty * 2.5,
    }
    return build_manifest(
        event_type="pos.transaction.completed",
        tenant_id=tenant,
        store_id=store,
        source_system="ncr-voyix",
        source_device_id=f"ncr-{sfx()}",
        payload=payload,
        source_seq=seq,
        idempotency_key=session_event_id or f"ncr:test:{uuid.uuid4().hex[:12]}",
    )


def cloud_ingest(events, verdicts=None, session_id=None, chain_tip=None):
    body = {
        "recovery_session_id": session_id or f"rec-{uuid.uuid4().hex[:12]}",
        "events": events,
        "verdicts": verdicts or [],
        "actor": "ops-1",
    }
    if chain_tip is not None:
        body["chain_tip"] = chain_tip
    return client.post("/api/v1/cloud/recovery/sessions", json=body)


def test_sale_batch_applies_and_is_idempotent():
    sku = f"CLOUD-{sfx()}"
    seed_inventory(sku, 100)
    events = [pos_event(sku, 2, i + 1) for i in range(3)]  # 3 transactions of 2 units = -6

    first = cloud_ingest(events)
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["events_applied"] == 3
    assert body["applied_deltas"][sku] == -6
    assert body["chain_tip_verified"] is True

    inv = [r for r in client.get("/api/v1/cloud/inventory?sku=" + sku).json()["inventory"]][0]
    assert inv["quantity"] == 94 or inv["on_hand_qty"] == 94

    # Replay the identical batch: no double decrement, no duplicate HITL items
    second = cloud_ingest(events)
    assert second.status_code == 200
    duplicate = second.json()
    assert duplicate["duplicates_skipped"] == 3
    assert duplicate["applied_deltas"] == {}

    inv = [r for r in client.get("/api/v1/cloud/inventory?sku=" + sku).json()["inventory"]][0]
    assert inv["quantity"] == 94 or inv["on_hand_qty"] == 94  # unchanged


def test_chain_tip_mismatch_rejects_batch():
    sku = f"CLOUD-{sfx()}"
    seed_inventory(sku, 50)
    events = [pos_event(sku, 1, i + 1) for i in range(2)]
    resp = cloud_ingest(events, chain_tip="0" * 64)
    assert resp.status_code == 409
    assert "chain tip" in resp.json()["detail"]
    inv = [r for r in client.get("/api/v1/cloud/inventory?sku=" + sku).json()["inventory"]][0]
    assert inv["quantity"] == 50


def test_duplicate_source_seq_rejected():
    sku = f"CLOUD-{sfx()}"
    seed_inventory(sku, 50)
    events = [pos_event(sku, 1, 1), pos_event(sku, 2, 1)]  # same seq twice
    resp = cloud_ingest(events)
    assert resp.status_code == 422


def test_oversell_goes_to_hitl_then_discard():
    sku = f"CLOUD-{sfx()}"
    seed_inventory(sku, 5)
    events = [pos_event(sku, 8, 1)]  # would oversell: 5 - 8 < 0
    resp = cloud_ingest(events)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["quarantined"] >= 1
    assert body["applied_deltas"] == {}

    queue = client.get("/api/v1/cloud/quarantine").json()["quarantine"]
    item = next(q for q in queue if q["sku"] == sku)
    assert item["payload"]["delta"] == -8

    resp = client.post(f"/api/v1/cloud/quarantine/{item['quarantine_id']}/resolve",
                       json={"resolution": "APPROVED_APPLY", "actor": "ops-1"})
    assert resp.status_code == 409  # still oversells against 5 on hand

    resp = client.post(f"/api/v1/cloud/quarantine/{item['quarantine_id']}/resolve",
                       json={"resolution": "REJECTED_VOID", "actor": "ops-1"})
    assert resp.status_code == 200
    inv = [r for r in client.get("/api/v1/cloud/inventory?sku=" + sku).json()["inventory"]][0]
    assert inv["quantity"] == 5 or inv["on_hand_qty"] == 5


def test_explicit_quarantine_verdict_then_apply_decrements_cloud_inventory():
    sku = f"CLOUD-{sfx()}"
    seed_inventory(sku, 100)
    event = pos_event(sku, 2, 31)
    resp = cloud_ingest([event], verdicts=[{"event_id": event["event_id"], "action": "quarantine", "reason": "review restock first"}])
    assert resp.status_code == 200
    assert resp.json()["quarantined"] >= 1
    assert client.get("/api/v1/cloud/inventory?sku=" + sku).json()["inventory"][0]["quantity"] == 100

    queue = client.get("/api/v1/cloud/quarantine").json()["quarantine"]
    item = next(q for q in queue if event["event_id"] in q["source_event_ids"])
    resp = client.post(f"/api/v1/cloud/quarantine/{item['quarantine_id']}/resolve",
                       json={"resolution": "APPROVED_APPLY", "actor": "ops-1"})
    assert resp.status_code == 200, resp.text
    assert client.get("/api/v1/cloud/inventory?sku=" + sku).json()["inventory"][0]["quantity"] == 98


def test_non_sale_event_goes_to_hitl_and_dedups():
    from app.edge.contracts import build_manifest

    sku = f"CLOUD-{sfx()}"
    seed_inventory(sku, 25)
    transfer = build_manifest(
        event_type="wms.transfer.observed",
        tenant_id="tenant_001",
        store_id="store_001",
        source_system="wms",
        source_device_id=f"wms-{sfx()}",
        payload={"sku": sku, "from_store": "DC", "quantity": 4},
        source_seq=1,
        idempotency_key=f"wms:test:{uuid.uuid4().hex[:12]}",
    )
    first = cloud_ingest([transfer])
    assert first.status_code == 200
    assert first.json()["quarantined"] == 1

    second = cloud_ingest([transfer])
    assert second.status_code == 200
    assert second.json()["duplicates_skipped"] == 1
    # exactly one HITL item from the two submissions
    queue = client.get("/api/v1/cloud/quarantine").json()["quarantine"]
    matches = [q for q in queue if transfer["event_id"] in q["source_event_ids"]]
    assert len(matches) == 1


def test_watermark_and_ledger_endpoints():
    sku = f"CLOUD-{sfx()}"
    seed_inventory(sku, 10)
    events = [pos_event(sku, 1, 11)]
    resp = cloud_ingest(events)
    assert resp.json()["events_applied"] == 1
    watermarks = client.get("/api/v1/cloud/watermarks").json()["watermarks"]
    assert any(w["store_id"] == "store_001" and w["last_acked_seq"] >= 11 for w in watermarks)
