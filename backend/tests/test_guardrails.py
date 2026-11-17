"""Guardrail tests: storage isolation, envelope completeness, two-step safety gates,
and the explicitly gated recovery sequence.

Isolated SQLite via a get_db dependency override; tests run in definition order.
"""

import os
import tempfile
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("DATABASE_URL", "sqlite:///./vejit.db")

from app.config import settings
from app.db.database import Base, get_db
import app.planes.models  # noqa: F401
import app.edge.models  # noqa: F401
import app.edge.integrity  # noqa: F401  (registers append-only enforcement)
from app.edge.contracts import ENVELOPE_FIELD_GROUPS, envelope_completeness
from app.edge.integrity import AppendOnlyViolation
from app.main import app

_tmp = os.path.join(tempfile.gettempdir(), f"vejit_guardrails_test_{uuid.uuid4().hex}.db")
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


def heartbeat(device_id, vendor="enterprise", interface="enterprise/erp-wms-supplier-v1"):
    resp = client.post("/api/v1/edge/devices/heartbeat", json={
        "device_id": device_id, "vendor": vendor, "interface": interface, "healthy": True,
    })
    assert resp.status_code == 201, resp.text


# ---------------------------------------------------------- envelope coverage

def test_every_adapter_generates_complete_envelope():
    from app.edge.adapters import AdapterContext, adapter_for

    ctx = lambda vendor, device: AdapterContext(  # noqa: E731
        tenant_id="t", store_id="s", source_system=vendor, device_id=device, source_seq=1,
    )
    samples = [
        ("ncr-voyix", {"msgType": "TX_COMPLETE", "txnId": "T1", "lines": [{"sku": "X", "qty": 1, "price": 1.0}], "total": 1.0}),
        ("toshiba-tcgs", {"eventType": "UPOS.TRANSACTION_COMPLETE", "transactionId": "T2", "items": [{"sku": "X", "qty": 1, "price": 1.0}], "total": 1.0}),
        ("diebold-nixdorf", {"deviceEvent": "BEETLE_SALE", "transactionId": "T3", "basket": [{"sku": "X", "qty": 1, "price": 1.0}], "total": 1.0}),
        ("scanner-scale", {"event": "WEIGHT_STABLE", "sku": "X", "weightGrams": 1000}),
        ("enterprise", {"event_type": "inventory.count.observed", "payload": {"sku": "X", "quantity": 1}}),
    ]
    for vendor, raw in samples:
        for manifest in adapter_for(vendor).translate(raw, ctx(vendor, f"dev-{sfx()}")):
            result = envelope_completeness(manifest)
            assert result["ok"], f"{vendor} produced an incomplete envelope: missing {result['missing']}"


def test_envelope_field_groups_match_spec():
    groups = {tuple(g) for g in ENVELOPE_FIELD_GROUPS}
    expected = {
        ("tenant_id", "store_id"),
        ("source_system", "source_device_id"),
        ("event_id", "idempotency_key"),
        ("schema_version",),
        ("source_seq", "occurred_at", "received_at"),
        ("correlation_id", "causation_id"),
        ("payload_hash",),
        ("auth_metadata",),
    }
    assert groups == expected


# ------------------------------------------------------------- append-only

def test_event_store_is_insert_only():
    from app.edge.models import EdgeEvent

    db = TestingSessionLocal()
    try:
        event = db.query(EdgeEvent).first()
        if event is None:
            client.post("/api/v1/edge/policy/seed-defaults", json={"actor": "test"})
            device = f"ent-{sfx()}"
            heartbeat(device)
            client.post("/api/v1/edge/ingest", json={
                "vendor": "enterprise", "device_id": device, "source_seq": 1,
                "raw": {"event_type": "inventory.count.observed", "payload": {"sku": f"SKU-{sfx()}", "quantity": 3}},
            })
            db.expire_all()
            event = db.query(EdgeEvent).first()
        assert event is not None
        event.payload = '{"tampered": true}'
        with pytest.raises(AppendOnlyViolation):
            db.commit()
        db.rollback()
        with pytest.raises(AppendOnlyViolation):
            db.delete(event)
            db.commit()
        db.rollback()
    finally:
        db.close()


def test_compare_and_swap_detects_concurrent_write():
    from app.db.models import InventoryItem
    from app.planes.models import PurchaseOrder
    from app.planes.operations import OptimisticConcurrencyError, cas_update

    db = TestingSessionLocal()
    try:
        item = InventoryItem(sku=f"CAS-{sfx()}", name="cas", quantity=10, reorder_point=1, unit_cost=1.0, version=1)
        db.add(item)
        db.commit()
        db.refresh(item)

        # Another "transaction" wins the row first
        expected_version = item.version  # capture before the competing writer (commit expires it)
        db.query(InventoryItem).filter(InventoryItem.id == item.id).update({"quantity": 99, "version": 2}, synchronize_session=False)
        db.commit()

        with pytest.raises(OptimisticConcurrencyError):
            cas_update(db, InventoryItem, item.id, expected_version, {"quantity": 42})
        db.rollback()
    finally:
        db.close()


# --------------------------------------------------- SAFE_MODE hard gates

def test_safe_mode_blocks_ingest_then_full_gated_recovery_restores():
    client.post("/api/v1/edge/policy/seed-defaults", json={"actor": "test-admin"})
    assert client.post("/api/v1/edge/continuity/transition",
                       json={"to_state": "SAFE_MODE", "actor": "safety-1", "reason": "test safety drill"}).status_code == 200

    device = f"ent-{sfx()}"
    heartbeat(device)
    resp = client.post("/api/v1/edge/ingest", json={
        "vendor": "enterprise", "device_id": device, "source_seq": 77,
        "raw": {"event_type": "inventory.count.observed", "payload": {"sku": "SAFEMODE-X", "quantity": 9}},
    })
    assert resp.json()["events"][0]["outcome"] == "blocked"
    assert resp.json()["events"][0]["authorization"] == "DENY"

    # Direct jump ONLINE is illegal even from SAFE_MODE
    assert client.post("/api/v1/edge/continuity/transition",
                       json={"to_state": "ONLINE", "actor": "op", "reason": "all good"}).status_code == 409
    assert client.post("/api/v1/edge/continuity/transition",
                       json={"to_state": "RECOVERING", "actor": "ops-1", "reason": "start recovery"}).status_code == 200

    # Sequence is enforced: hash_verification before connectivity_check is rejected
    assert client.post("/api/v1/edge/recovery/hash-verification", json={"actor": "ops-1"}).status_code == 409
    assert client.post("/api/v1/edge/recovery/connectivity-check", json={"actor": "ops-1"}).json()["ok"] is True
    assert client.post("/api/v1/edge/recovery/hash-verification", json={"actor": "ops-1"}).json()["ok"] is True
    assert client.post("/api/v1/edge/recovery/watermark-replay", json={"actor": "ops-1"}).json()["ok"] is True
    ack = client.post("/api/v1/edge/recovery/cloud-ack",
                      json={"actor": "ops-1", "resolution": "accept_local", "ack_mode": "local_override"})
    assert ack.status_code == 200, ack.text
    assert ack.json()["transition"]["to_state"] == "ONLINE"


def test_stale_policy_bundle_closes_the_gate():
    original = settings.POLICY_BUNDLE_MAX_AGE_SECONDS
    try:
        settings.POLICY_BUNDLE_MAX_AGE_SECONDS = 0.0001  # everything is stale
        resp = client.post("/api/v1/operations/adjustments",
                           json={"sku": f"GATE-{sfx()}", "quantity_delta": 1, "actor": "ops-1"})
        assert resp.status_code == 409
        gates = client.get("/api/v1/edge/status").json()
        assert gates is not None  # status path still available; actions are not
    finally:
        settings.POLICY_BUNDLE_MAX_AGE_SECONDS = original
