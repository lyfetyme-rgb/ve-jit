"""Store Console — multi-tenant retail app on the governed VE-JIT core."""

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
import app.integrations.models  # noqa: F401
import app.console.models  # noqa: F401
from app.console.seed import seed_demo
from app.edge.policy import seed_default_policies
from app.main import app

_tmp = os.path.join(tempfile.gettempdir(), f"vejit_console_test_{uuid.uuid4().hex}.db")
_engine = create_engine(f"sqlite:///{_tmp}", connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)


def override():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def _isolate():
    app.dependency_overrides[get_db] = override
    Base.metadata.create_all(bind=_engine)
    db = TestingSessionLocal()
    seed_default_policies(db)
    seed_demo(db)
    db.close()
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_db, None)


client = TestClient(app)


def test_catalog_has_three_retail_verticals():
    body = client.get("/api/v1/console/catalog").json()
    tenants = {t["tenant_id"]: t for t in body["tenants"]}
    assert {"foodlion", "acehardware", "lowes"} <= set(tenants)
    assert tenants["foodlion"]["vertical"] == "grocery"
    assert tenants["acehardware"]["vertical"] == "hardware"
    stores = {s["store_id"]: s for s in body["stores"]}
    assert stores["ACE-NC-001"]["architecture"] == "arm64"
    assert stores["LOW-NC-DC1"]["architecture"] == "amd64"


def test_seed_is_idempotent():
    db = TestingSessionLocal()
    result = seed_demo(db)
    db.close()
    assert result == {"tenants": 0, "stores": 0, "skus": 0, "orders": 0, "recommendations": 0}


def test_tenant_scoped_overview_and_inventory():
    foodlion = client.get("/api/v1/console/overview?tenant_id=foodlion&store_id=FL-VA-042").json()
    ace = client.get("/api/v1/console/overview?tenant_id=acehardware&store_id=ACE-NC-001").json()
    assert foodlion["skus"] == 4
    assert ace["skus"] == 4
    assert foodlion["open_recommendations"] >= 1

    fl_inv = client.get("/api/v1/console/inventory?tenant_id=foodlion&store_id=FL-VA-042").json()["items"]
    ace_inv = client.get("/api/v1/console/inventory?tenant_id=acehardware&store_id=ACE-NC-001").json()["items"]
    assert all(i["sku"].startswith("FL-") for i in fl_inv)
    assert all(i["sku"].startswith("ACE-") for i in ace_inv)


def test_cross_tenant_store_is_rejected():
    resp = client.get("/api/v1/console/inventory?tenant_id=foodlion&store_id=ACE-NC-001")
    assert resp.status_code == 404


def test_console_ui_mounted():
    assert client.get("/console/").status_code == 200
    html = client.get("/console/").text
    assert "VE-JIT Store Console" in html
    assert 'src="app.js"' in html


def test_pos_sale_and_governed_recommendation_flow():
    sale = client.post("/api/v1/console/orders", json={
        "tenant_id": "foodlion", "store_id": "FL-VA-042", "channel": "pos",
        "lines": [{"sku": "FL-MILK-1G", "quantity": 2}],
        "client_order_id": "console-test-1",
    })
    assert sale.status_code == 201, sale.text
    assert sale.json()["total"] == round(3.79 * 2, 2)

    rec = client.get("/api/v1/console/recommendations?tenant_id=foodlion&store_id=FL-VA-042").json()["recommendations"][0]
    uid = rec["uid"]

    # Execute before approval must be refused by the governance gate.
    early = client.post(f"/api/v1/console/recommendations/{uid}/execute", json={"actor": "ops-1"})
    assert early.status_code == 409

    approved = client.post(f"/api/v1/console/recommendations/{uid}/approve", json={
        "actor": "manager-1", "tenant_id": "foodlion", "note": "console test"
    })
    assert approved.status_code == 200, approved.text
    executed = client.post(f"/api/v1/console/recommendations/{uid}/execute", json={"actor": "ops-1"})
    assert executed.status_code == 200, executed.text
    assert executed.json()["recommendation"]["status"] == "EXECUTED"

    audit = client.get("/api/v1/console/audit?tenant_id=foodlion&store_id=FL-VA-042").json()
    assert any(a["uid"] == uid for a in audit["authorized_actions"])


def test_console_rejects_cardholder_data():
    payload = {
        "tenant_id": "lowes", "store_id": "LOW-NC-DC1", "channel": "pos",
        "lines": [{"sku": "LOW-LUMBER-2X4", "quantity": 1}],
        "card_number": "4111111111111111",
    }
    resp = client.post("/api/v1/console/orders", json=payload)
    assert resp.status_code == 422
    assert "4111111111111111" not in resp.text
