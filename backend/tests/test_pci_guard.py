"""PCI-DSS scope boundary: VE-JIT never receives, stores or logs cardholder data."""

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
from app.pci_guard import contains_cardholder_data, luhn_ok, redact
import app.planes.models  # noqa: F401
import app.edge.models  # noqa: F401
import app.cloud.models  # noqa: F401
from app.main import app

_tmp = os.path.join(tempfile.gettempdir(), f"vejit_pci_test_{uuid.uuid4().hex}.db")
engine = create_engine(f"sqlite:///{_tmp}", connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def _isolate_db():
    app.dependency_overrides[get_db] = override_get_db
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_db, None)


Base.metadata.create_all(bind=engine)
client = TestClient(app)
PAN = "4111111111111111"  # Luhn-valid test PAN


def sfx():
    return uuid.uuid4().hex[:8].upper()


def test_luhn_helpers():
    assert luhn_ok("4111111111111111")
    assert luhn_ok("5500005555555559")
    assert not luhn_ok("4111111111111112")
    assert not luhn_ok("123456")  # too short


def test_detector_finds_pan_and_track_data():
    assert contains_cardholder_data({"order": f"card {PAN}"})
    assert contains_cardholder_data({"order": "%B4111111111111111^DOE/JOHN^2505101234567890?"})
    assert contains_cardholder_data({"payload": {"line": "pay with 5500005555555559 please"}})
    assert contains_cardholder_data({"card_number": PAN})          # key-name rule
    assert contains_cardholder_data({"customer": {"cvv": "123"}})   # key-name rule
    assert not contains_cardholder_data({"sku": "DAIRY-001", "quantity": 2, "store_id": "store_001"})


def test_iot_normalize_rejects_cardholder_data_with_sanitized_error():
    resp = client.post("/api/v1/iot/normalize", json={
        "device_id": "tc52-014", "sku": "MILK-1GAL", "quantity": 1,
        "note": f"customer tapped {PAN}",
    })
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert PAN not in detail
    assert PAN not in json.dumps(resp.json())


def test_edge_ingest_rejects_track_data():
    resp = client.post("/api/v1/edge/ingest", json={
        "vendor": "ncr-voyix", "device_id": f"ncr-{sfx()}", "source_seq": 1,
        "raw": {"msgType": "TX_COMPLETE", "txnId": f"T-{sfx()}",
                "lines": [{"sku": "X", "qty": 1, "price": 1.0}],
                "track": "%B4111111111111111^DOE/JOHN^2505101234567890?"},
    })
    assert resp.status_code == 422
    assert PAN not in resp.text


def test_pos_transaction_rejects_card_named_fields():
    sku = f"PCI-{sfx()}"
    price = client.post("/api/v1/commerce/prices", json={"sku": sku, "unit_price": 2.5})
    assert price.status_code == 201
    item = client.post("/api/v1/inventory", json={
        "sku": sku, "name": sku, "quantity": 10, "reorder_point": 1, "unit_cost": 1.0,
    })
    assert item.status_code == 200
    resp = client.post("/api/v1/commerce/pos/transactions", json={
        "register_id": "lane-9", "payment_method": "card",
        "card_number": PAN,  # attacker tries to smuggle a PAN through an unknown field
        "lines": [{"sku": sku, "quantity": 1}],
    })
    assert resp.status_code == 422
    assert PAN not in resp.text


def test_clean_inventory_telemetry_is_accepted():
    # A 13-digit non-PAN digit string (fails Luhn) must pass through
    code = "1234567890123"
    assert not luhn_ok(code)
    resp = client.post("/api/v1/iot/normalize", json={
        "device_id": "tc52-014", "sku": code, "quantity": 1, "store_id": "store_001",
    })
    assert resp.status_code == 200


def test_redact_scrubs_pan_class_material():
    masked = redact(f"order PAN {PAN} and ok 123456")
    assert PAN not in masked
    assert "[REDACTED-PAN]" in masked
    assert redact(f"keep {PAN}") == "keep [REDACTED-PAN]"
    assert redact({"card_number": PAN}) == {"card_number": "[REDACTED-CARD-FIELD]"}


def test_compliance_pci_scope_endpoint_declares_out_of_scope():
    resp = client.get("/api/v1/compliance/pci-scope")
    assert resp.status_code == 200
    body = resp.json()
    assert body["scope"] == "out-of-scope"
    assert "PAN" in " ".join(body["never_ingests"])
    assert any("422" in e for e in body["enforcement"])
    assert any("sandbox" in c for c in body["controls"])
