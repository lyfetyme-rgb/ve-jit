"""Store Console — multi-tenant retail app on the governed VE-JIT core."""

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
import app.integrations.models  # noqa: F401
import app.console.models  # noqa: F401
from app.console.seed import load_retailer_config, seed_demo, seed_retailers
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
    assert {"grocery_demo", "hardware_demo", "home_improvement_demo"} <= set(tenants)
    assert tenants["grocery_demo"]["vertical"] == "grocery"
    assert tenants["hardware_demo"]["vertical"] == "hardware"
    stores = {s["store_id"]: s for s in body["stores"]}
    assert stores["HW-NC-001"]["architecture"] == "arm64"
    assert stores["HI-NC-DC1"]["architecture"] == "amd64"


def test_seed_is_idempotent():
    db = TestingSessionLocal()
    result = seed_demo(db)
    db.close()
    assert result == {"tenants": 0, "stores": 0, "skus": 0, "orders": 0, "recommendations": 0}


def test_tenant_scoped_overview_and_inventory():
    grocery_demo = client.get("/api/v1/console/overview?tenant_id=grocery_demo&store_id=GR-VA-042").json()
    hardware = client.get("/api/v1/console/overview?tenant_id=hardware_demo&store_id=HW-NC-001").json()
    assert grocery_demo["skus"] == 4
    assert hardware["skus"] == 4
    assert grocery_demo["open_recommendations"] >= 1

    grocery_inv = client.get("/api/v1/console/inventory?tenant_id=grocery_demo&store_id=GR-VA-042").json()["items"]
    hardware_inv = client.get("/api/v1/console/inventory?tenant_id=hardware_demo&store_id=HW-NC-001").json()["items"]
    assert all(i["sku"].startswith("GR-") for i in grocery_inv)
    assert all(i["sku"].startswith("HW-") for i in hardware_inv)


def test_cross_tenant_store_is_rejected():
    resp = client.get("/api/v1/console/inventory?tenant_id=grocery_demo&store_id=HW-NC-001")
    assert resp.status_code == 404


def test_console_ui_mounted():
    assert client.get("/console/").status_code == 200
    html = client.get("/console/").text
    assert "VE-JIT Store Console" in html
    assert 'src="app.js"' in html


def test_pos_sale_and_governed_recommendation_flow():
    sale = client.post("/api/v1/console/orders", json={
        "tenant_id": "grocery_demo", "store_id": "GR-VA-042", "channel": "pos",
        "lines": [{"sku": "GR-MILK-1G", "quantity": 2}],
        "client_order_id": "console-test-1",
    })
    assert sale.status_code == 201, sale.text
    assert sale.json()["total"] == round(3.79 * 2, 2)

    rec = client.get("/api/v1/console/recommendations?tenant_id=grocery_demo&store_id=GR-VA-042").json()["recommendations"][0]
    uid = rec["uid"]

    # Execute before approval must be refused by the governance gate.
    early = client.post(f"/api/v1/console/recommendations/{uid}/execute", json={"actor": "test-principal"})
    assert early.status_code == 409

    approved = client.post(f"/api/v1/console/recommendations/{uid}/approve", json={
        "actor": "test-principal", "tenant_id": "grocery_demo", "note": "console test"
    })
    assert approved.status_code == 200, approved.text
    assert approved.json()["approved_by"] == "test-principal"
    executed = client.post(f"/api/v1/console/recommendations/{uid}/execute", json={"actor": "test-principal"})
    assert executed.status_code == 200, executed.text
    assert executed.json()["recommendation"]["status"] == "EXECUTED"

    audit = client.get("/api/v1/console/audit?tenant_id=grocery_demo&store_id=GR-VA-042").json()
    assert any(a["uid"] == uid for a in audit["authorized_actions"])


def test_custom_catalog_loads_arbitrary_company():
    """Any retailer can be added via config — no code changes, no fixed names."""
    custom = {
        "retailers": [{
            "tenant_id": "my_customer",
            "name": "My Customer",
            "vertical": "grocery",
            "description": "A company loaded from a custom catalog",
            "sku_prefix": "MC-",
            "stores": [{
                "store_id": "MC-001", "name": "Main Store", "location": "Anywhere",
                "architecture": "amd64", "channel": "store",
            }],
            "catalog": [
                {"sku": "MC-100", "name": "Example Item", "price": 4.99,
                 "quantity": 40, "reorder_point": 10}
            ],
        }]
    }
    db = TestingSessionLocal()
    result = seed_retailers(db, custom)
    db.close()
    assert result["tenants"] == 1 and result["stores"] == 1 and result["skus"] == 1

    catalog = client.get("/api/v1/console/catalog").json()
    assert any(t["tenant_id"] == "my_customer" and t["name"] == "My Customer" for t in catalog["tenants"])
    overview = client.get("/api/v1/console/overview?tenant_id=my_customer&store_id=MC-001").json()
    assert overview["skus"] == 1

    items = client.get("/api/v1/console/inventory?tenant_id=my_customer&store_id=MC-001").json()["items"]
    assert items[0]["sku"] == "MC-100"


def test_second_custom_load_is_idempotent():
    custom = {
        "retailers": [{
            "tenant_id": "idem_customer", "name": "Idem Co", "vertical": "hardware",
            "sku_prefix": "IC-",
            "stores": [{"store_id": "IC-001", "name": "Store"}],
            "catalog": [
                {"sku": "IC-1", "name": "Item", "price": 1.0, "quantity": 5, "reorder_point": 2}
            ],
        }]
    }
    db = TestingSessionLocal()
    first = seed_retailers(db, custom)
    second = seed_retailers(db, custom)
    db.close()
    assert first["tenants"] == 1
    assert second == {"tenants": 0, "stores": 0, "skus": 0, "orders": 0, "recommendations": 0}


def test_catalog_validation_rejects_bad_config():
    path = os.path.join(tempfile.gettempdir(), f"bad_retailers_{uuid.uuid4().hex}.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"retailers": [{"tenant_id": "x"}]}, handle)
    with pytest.raises(ValueError, match="missing required field"):
        load_retailer_config(path)
    os.unlink(path)


def test_console_rejects_cardholder_data():
    payload = {
        "tenant_id": "home_improvement_demo", "store_id": "HI-NC-DC1", "channel": "pos",
        "lines": [{"sku": "HI-LUMBER-2X4", "quantity": 1}],
        "card_number": "4111111111111111",
    }
    resp = client.post("/api/v1/console/orders", json=payload)
    assert resp.status_code == 422
    assert "4111111111111111" not in resp.text
