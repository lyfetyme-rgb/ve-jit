"""Watermark replay endpoint (sync-go classified deltas) and HITL resolution."""

import json
import os
import tempfile
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("DATABASE_URL", "sqlite:///./vejit.db")

from app.cloud.reconcile import compute_chain_tip
from app.db.database import Base, get_db
import app.planes.models  # noqa: F401
import app.edge.models  # noqa: F401
import app.cloud.models  # noqa: F401
from app.edge.contracts import build_manifest
from app.main import app

_tmp = os.path.join(tempfile.gettempdir(), f"vejit_replay_test_{uuid.uuid4().hex}.db")
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
TENANT = "tenant_001"


def sfx():
    return uuid.uuid4().hex[:8].upper()


def store():
    return f"store_{sfx()}"


def pos_event(sku, qty, seq, store_id):
    return build_manifest(
        event_type="pos.transaction.completed",
        tenant_id=TENANT,
        store_id=store_id,
        source_system="ncr-voyix",
        source_device_id=f"ncr-{sfx()}",
        payload={
            "transaction_id": f"T-{sfx()}",
            "lane_id": f"lane-{sfx()}",
            "lines": [{"sku": sku, "quantity": qty, "unit_price": 2.5}],
            "total": qty * 2.5,
        },
        source_seq=seq,
        idempotency_key=f"ncr:replay:{uuid.uuid4().hex[:12]}",
    )


def ingest(events, store_id):
    return client.post("/api/v1/cloud/recovery/sessions", json={
        "recovery_session_id": f"rs-{uuid.uuid4().hex[:12]}",
        "store_id": store_id,
        "events": events,
        "verdicts": [],
        "actor": "ops-1",
    })


def watermark_tip_and_seq(store_id):
    row = next(w for w in client.get("/api/v1/cloud/watermarks").json()["watermarks"] if w["store_id"] == store_id)
    return row["last_verified_audit_hash"], row["last_acked_seq"]


def cloud_qty(sku):
    for row in client.get("/api/v1/cloud/inventory?sku=" + sku).json()["inventory"]:
        if row["sku"] == sku:
            return row["quantity"]
    return None


def test_watermark_replay_404_for_unknown_store():
    resp = client.post("/api/v1/cloud/edge-sync/watermark-replay", json={
        "tenant_id": "nope", "store_id": "nope", "recovery_session_id": "rs-1",
        "from_seq": 0, "to_seq": 1,
        "prev_audit_hash": "GENESIS", "tip_audit_hash": "0" * 64,
        "batches": [{"sku": "X", "verdict": "COMMUTE_APPLY", "net_delta_qty": -1,
                     "baseline_cloud_qty": 1, "source_event_ids": ["e1"]}],
    })
    assert resp.status_code == 404


def test_watermark_replay_full_flow_and_idempotent_retry():
    s = store()
    sku = f"RPL-{sfx()}"
    seed = client.post("/api/v1/cloud/inventory/seed", json={"sku": sku, "quantity": 100, "actor": "ops", "store_id": s})
    assert seed.status_code == 201

    batch1 = [pos_event(sku, 2, 1, s), pos_event(sku, 2, 2, s)]
    resp = ingest(batch1, s)
    assert resp.status_code == 200, resp.text
    tip, seq = watermark_tip_and_seq(s)
    assert seq == 2
    assert cloud_qty(sku) == 96

    event3 = pos_event(sku, 1, 3, s)
    tip3 = compute_chain_tip(tip, [event3["payload_hash"]])
    replay = client.post("/api/v1/cloud/edge-sync/watermark-replay", json={
        "tenant_id": TENANT, "store_id": s,
        "recovery_session_id": "rs-flow-1",
        "from_seq": 2, "to_seq": 3,
        "prev_audit_hash": tip, "tip_audit_hash": tip3,
        "batches": [{
            "sku": sku, "verdict": "COMMUTE_APPLY", "net_delta_qty": -1,
            "baseline_cloud_qty": 96, "source_event_ids": [event3["event_id"]],
        }],
    })
    assert replay.status_code == 200, replay.text
    body = replay.json()
    assert body["applied_skus"] == 1
    assert body["quarantined_skus"] == 0
    assert body["acked_seq"] == 3
    assert cloud_qty(sku) == 95

    retry = client.post("/api/v1/cloud/edge-sync/watermark-replay", json={
        "tenant_id": TENANT, "store_id": s,
        "recovery_session_id": "rs-flow-1",
        "from_seq": 2, "to_seq": 3,
        "prev_audit_hash": tip, "tip_audit_hash": tip3,
        "batches": [{
            "sku": sku, "verdict": "COMMUTE_APPLY", "net_delta_qty": -1,
            "baseline_cloud_qty": 96, "source_event_ids": [event3["event_id"]],
        }],
    })
    assert retry.status_code == 200
    assert retry.json()["applied_skus"] == 0
    assert cloud_qty(sku) == 95

    bad = client.post("/api/v1/cloud/edge-sync/watermark-replay", json={
        "tenant_id": TENANT, "store_id": s,
        "recovery_session_id": "rs-flow-2",
        "from_seq": 0, "to_seq": 4,
        "prev_audit_hash": tip, "tip_audit_hash": tip3,
        "batches": [{
            "sku": sku, "verdict": "COMMUTE_APPLY", "net_delta_qty": -1,
            "baseline_cloud_qty": 95, "source_event_ids": ["e-new"],
        }],
    })
    assert bad.status_code == 409


def test_oversell_classified_apply_is_diverted_to_hitl():
    s = store()
    sku = f"RPL-{sfx()}"
    seed = client.post("/api/v1/cloud/inventory/seed", json={"sku": sku, "quantity": 2, "actor": "ops", "store_id": s})
    assert seed.status_code == 201

    batch1 = [pos_event(sku, 1, 1, s)]
    resp = ingest(batch1, s)
    tip, seq = watermark_tip_and_seq(s)
    assert cloud_qty(sku) == 1

    event2 = pos_event(sku, 5, 2, s)
    tip2 = compute_chain_tip(tip, [event2["payload_hash"]])
    resp = client.post("/api/v1/cloud/edge-sync/watermark-replay", json={
        "tenant_id": TENANT, "store_id": s,
        "recovery_session_id": "rs-oversell-1",
        "from_seq": 1, "to_seq": 2,
        "prev_audit_hash": tip, "tip_audit_hash": tip2,
        "batches": [{
            "sku": sku, "verdict": "COMMUTE_APPLY", "net_delta_qty": -5,
            "baseline_cloud_qty": 1, "source_event_ids": [event2["event_id"]],
        }],
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["quarantined_skus"] == 1
    assert body["applied_skus"] == 0
    assert cloud_qty(sku) == 1

    queue = client.get("/api/v1/cloud/quarantine").json()["quarantine"]
    item = next(q for q in queue if q["sku"] == sku)
    assert item["status"] == "PENDING_REVIEW"
    assert item["edge_delta_qty"] == -5


def test_quarantine_then_approve_apply_then_rejected_after_resolution():
    s = store()
    sku = f"RPL-{sfx()}"
    seed = client.post("/api/v1/cloud/inventory/seed", json={"sku": sku, "quantity": 100, "actor": "ops", "store_id": s})
    assert seed.status_code == 201

    batch1 = [pos_event(sku, 1, 1, s)]
    resp = ingest(batch1, s)
    tip, seq = watermark_tip_and_seq(s)

    event2 = pos_event(sku, 7, 2, s)
    tip2 = compute_chain_tip(tip, [event2["payload_hash"]])
    resp = client.post("/api/v1/cloud/edge-sync/watermark-replay", json={
        "tenant_id": TENANT, "store_id": s,
        "recovery_session_id": "rs-hitl-1",
        "from_seq": 1, "to_seq": 2,
        "prev_audit_hash": tip, "tip_audit_hash": tip2,
        "batches": [{
            "sku": sku, "verdict": "QUARANTINE", "net_delta_qty": -7,
            "baseline_cloud_qty": 99, "conflict_reason": "RESTOCK_VS_PENDING_SALE",
            "source_event_ids": [event2["event_id"]],
        }],
    })
    assert resp.status_code == 200
    qid = resp.json()["quarantine_ids"][0]

    resolve = client.post(f"/api/v1/cloud/quarantine/{qid}/resolve",
                          json={"resolution": "APPROVED_APPLY", "actor": "ops-1", "note": "verified against DC transfer"})
    assert resolve.status_code == 200, resolve.text
    assert cloud_qty(sku) == 92

    again = client.post(f"/api/v1/cloud/quarantine/{qid}/resolve",
                        json={"resolution": "REJECTED_VOID", "actor": "ops-1"})
    assert again.status_code == 409

    queue = client.get("/api/v1/cloud/quarantine?status=resolved").json()["quarantine"]
    item = next(q for q in queue if q["quarantine_id"] == qid)
    assert item["status"] == "APPROVED_APPLY"
    assert item["resolution_note"] == "verified against DC transfer"
