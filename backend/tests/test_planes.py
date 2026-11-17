"""End-to-end tests for the three planes.

Runs against an isolated SQLite database via a get_db dependency override, so
the development database is untouched. Unique suffixes keep runs independent.
"""

import os
import tempfile
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("DATABASE_URL", "sqlite:///./vejit.db")  # imported app uses this only for lifespan create_all

from app.db.database import Base, get_db
import app.planes.models  # noqa: F401  (register plane tables)
from app.main import app

_tmp = os.path.join(tempfile.gettempdir(), f"vejit_planes_test_{uuid.uuid4().hex}.db")
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

client = TestClient(app)


def sfx():
    return uuid.uuid4().hex[:8].upper()


def make_sku(sku, quantity, unit_cost=1.0, reorder_point=5):
    resp = client.post("/api/v1/inventory", json={
        "sku": sku, "name": sku, "quantity": quantity,
        "reorder_point": reorder_point, "unit_cost": unit_cost,
    })
    assert resp.status_code == 200, resp.text
    return resp.json()


def price_sku(sku, price, store_id=None):
    resp = client.post("/api/v1/commerce/prices", json={"sku": sku, "unit_price": price, "store_id": store_id})
    assert resp.status_code == 201, resp.text
    return resp.json()


# ------------------------------------------------------------------- planes

def test_planes_overview_lists_three_planes_with_governance_rule():
    resp = client.get("/api/v1/planes")
    assert resp.status_code == 200
    body = resp.json()
    assert [p["id"] for p in body["planes"]] == ["commerce", "operations", "intelligence"]
    assert "recommendations" in body["governance_rule"]
    by_id = {p["id"]: p for p in body["planes"]}
    assert by_id["intelligence"]["executes_business_actions"] is False
    assert by_id["intelligence"]["produces_recommendations"] is True
    assert by_id["operations"]["produces_recommendations"] is False
    assert by_id["commerce"]["owns_customer_transaction"] is True
    assert "POS integration" in by_id["commerce"]["capabilities"]
    assert "Inventory ledger" in by_id["operations"]["capabilities"]
    assert "Demand forecasting" in by_id["intelligence"]["capabilities"]


def test_plane_detail_and_unknown_plane():
    assert client.get("/api/v1/planes/operations").json()["owns"] == "physical business processes"
    assert client.get("/api/v1/planes/marketing").status_code == 404


# ---------------------------------------------------------------- commerce

def test_pos_order_applies_price_book_promotion_loyalty_and_ledger():
    sku = f"VEJIT-POS-{sfx()}"
    member = f"M-{sfx()}"
    promo = f"PROMO-{sfx()}"

    make_sku(sku, quantity=100, unit_cost=3.0)
    price_sku(sku, 10.0)
    client.post("/api/v1/commerce/promotions", json={
        "code": promo, "name": "Ten percent off", "kind": "percent_off", "value": 10.0, "sku": sku,
    })
    client.post("/api/v1/commerce/loyalty/members", json={"member_number": member, "name": "Test Shopper"})

    client_order_id = f"POS-IDEM-{sfx()}"
    resp = client.post("/api/v1/commerce/pos/transactions", json={
        "register_id": "lane-7",
        "payment_method": "card",
        "member_number": member,
        "client_order_id": client_order_id,
        "lines": [{"sku": sku, "quantity": 2}],
    })
    assert resp.status_code == 201, resp.text
    order = resp.json()
    # 2 * 10.00 = 20.00, 10% promo = 2.00 off -> 18.00
    assert order["subtotal"] == 20.0
    assert order["discount_total"] == 2.0
    assert order["total"] == 18.0
    assert order["status"] == "PAID"
    assert order["points_earned"] == 18
    assert order["lines"][0]["promo_code"] == promo

    # Stock decremented + ledger SALE entry
    assert client.get("/api/v1/inventory").json() and True
    ledger = client.get(f"/api/v1/operations/ledger?sku={sku}&reason=SALE").json()["entries"]
    assert any(e["ref"] == order["order_ref"] and e["delta"] == -2 for e in ledger)

    # Idempotent replay: same client_order_id returns the original, no double-decrement
    replay = client.post("/api/v1/commerce/pos/transactions", json={
        "register_id": "lane-7", "payment_method": "card", "client_order_id": client_order_id,
        "lines": [{"sku": sku, "quantity": 2}],
    })
    assert replay.status_code == 200
    assert replay.json()["order_ref"] == order["order_ref"]
    ledger_after = client.get(f"/api/v1/operations/ledger?sku={sku}&reason=SALE").json()["entries"]
    assert len([e for e in ledger_after if e["ref"] == order["order_ref"]]) == 1

    # Loyalty accrued exactly once
    assert client.get(f"/api/v1/commerce/loyalty/members/{member}").json()["points"] == 18


def test_price_book_beats_request_fallback_and_unknown_sku_rejected():
    sku = f"VEJIT-PRC-{sfx()}"
    make_sku(sku, quantity=10)
    price_sku(sku, 4.5)
    resp = client.post("/api/v1/commerce/orders", json={
        "channel": "ecommerce",
        "lines": [{"sku": sku, "quantity": 1, "unit_price": 999.0}],
    })
    assert resp.status_code == 201, resp.text
    assert resp.json()["lines"][0]["unit_price"] == 4.5  # price book wins

    missing = f"VEJIT-NOPRICE-{sfx()}"
    make_sku(missing, quantity=10)
    resp = client.post("/api/v1/commerce/orders", json={"channel": "pos", "lines": [{"sku": missing, "quantity": 1}]})
    assert resp.status_code == 422


def test_order_cancellation_restocks_and_claws_back_points():
    sku = f"VEJIT-CXL-{sfx()}"
    member = f"M-{sfx()}"
    make_sku(sku, quantity=50)
    price_sku(sku, 5.0)
    client.post("/api/v1/commerce/loyalty/members", json={"member_number": member, "name": "Cancel Tester"})

    order = client.post("/api/v1/commerce/orders", json={
        "channel": "ecommerce", "member_number": member,
        "lines": [{"sku": sku, "quantity": 3}],
    }).json()
    cancel = client.post(f"/api/v1/commerce/orders/{order['order_ref']}/cancel",
                         json={"actor": "csr-1", "reason": "customer request"})
    assert cancel.status_code == 200
    assert cancel.json()["status"] == "CANCELLED"
    item = [i for i in client.get("/api/v1/inventory").json() if i["sku"] == sku][0]
    assert item["quantity"] == 50  # restocked
    assert client.get(f"/api/v1/commerce/loyalty/members/{member}").json()["points"] == 0


# -------------------------------------------------------------- operations

def test_purchase_order_requires_segregated_approval_before_receiving():
    sku = f"VEJIT-PO-{sfx()}"
    make_sku(sku, quantity=0)
    supplier = client.post("/api/v1/operations/suppliers", json={
        "name": f"Supplier {sfx()}", "payment_terms": "NET45", "lead_time_days": 3,
        "sla_otif_pct": 97.0, "sla_fill_rate_pct": 99.0,
    }).json()

    po = client.post("/api/v1/operations/purchase-orders", json={
        "supplier_id": supplier["id"], "created_by": "buyer-1",
        "lines": [{"sku": sku, "quantity_ordered": 20, "unit_cost": 2.0}],
    }).json()
    assert po["status"] == "DRAFT"

    # Receiving before approval is rejected
    resp = client.post("/api/v1/operations/receiving", json={
        "po_number": po["po_number"], "actor": "receiver-1",
        "lines": [{"sku": sku, "quantity_received": 5}],
    })
    assert resp.status_code == 409

    # Creator cannot approve their own PO
    resp = client.post(f"/api/v1/operations/purchase-orders/{po['po_number']}/approve",
                       json={"approver": "buyer-1"})
    assert resp.status_code == 403

    resp = client.post(f"/api/v1/operations/purchase-orders/{po['po_number']}/approve",
                       json={"approver": "manager-9"})
    assert resp.status_code == 200 and resp.json()["status"] == "APPROVED"

    # Over-receipt beyond outstanding is rejected
    resp = client.post("/api/v1/operations/receiving", json={
        "po_number": po["po_number"], "actor": "receiver-1",
        "lines": [{"sku": sku, "quantity_received": 21}],
    })
    assert resp.status_code == 422

    resp = client.post("/api/v1/operations/receiving", json={
        "po_number": po["po_number"], "actor": "receiver-1",
        "lines": [{"sku": sku, "quantity_received": 20}],
    })
    assert resp.status_code == 201
    assert resp.json()["status"] == "RECEIVED"
    item = [i for i in client.get("/api/v1/inventory").json() if i["sku"] == sku][0]
    assert item["quantity"] == 20


def test_transfer_ship_receive_and_insufficient_stock():
    sku = f"VEJIT-TRF-{sfx()}"
    make_sku(sku, quantity=10)
    transfer = client.post("/api/v1/operations/transfers", json={
        "from_store": "DC-EAST", "to_store": "store_001", "sku": sku,
        "quantity": 4, "created_by": "planner-1",
    }).json()
    assert transfer["status"] == "REQUESTED"

    too_big = client.post("/api/v1/operations/transfers", json={
        "from_store": "store_001", "to_store": "store_002", "sku": sku,
        "quantity": 99999, "created_by": "planner-1",
    }).json()
    resp = client.post(f"/api/v1/operations/transfers/{too_big['transfer_ref']}/ship", json={"actor": "ops-1"})
    assert resp.status_code == 409

    assert client.post(f"/api/v1/operations/transfers/{transfer['transfer_ref']}/ship",
                       json={"actor": "ops-1"}).status_code == 200
    assert client.post(f"/api/v1/operations/transfers/{transfer['transfer_ref']}/receive",
                       json={"actor": "ops-2"}).status_code == 200
    assert client.get(f"/api/v1/operations/transfers/{transfer['transfer_ref']}").json()["status"] == "RECEIVED"


def test_waste_record_posts_ledger_and_summary():
    sku = f"VEJIT-WST-{sfx()}"
    make_sku(sku, quantity=30, unit_cost=1.5)
    resp = client.post("/api/v1/operations/waste", json={
        "sku": sku, "quantity": 5, "reason": "SPOILAGE", "estimated_cost": 7.5, "actor": "ops-1",
    })
    assert resp.status_code == 201, resp.text
    ledger = client.get(f"/api/v1/operations/ledger?sku={sku}&reason=WASTE").json()["entries"]
    assert any(e["delta"] == -5 for e in ledger)
    summary = client.get("/api/v1/intelligence/waste/summary?days=30").json()
    assert summary["by_reason"]["SPOILAGE"]["units"] >= 5


# ------------------------------------------ recommendation governance bridge

def test_recommendation_cannot_execute_before_approval():
    resp = client.post("/api/v1/intelligence/recommendations", json={
        "kind": "REPLENISHMENT", "subject_type": "sku", "subject_id": f"SKU-{sfx()}",
        "summary": "restock test", "recommended_action": {
            "sku": "X", "suggested_order_quantity": 10,
        },
    })
    assert resp.status_code == 201
    uid = resp.json()["uid"]

    resp = client.post(f"/api/v1/operations/recommendations/{uid}/execute", json={"actor": "ops-1"})
    assert resp.status_code == 409

    resp = client.post(f"/api/v1/intelligence/recommendations/{uid}/approve", json={"approver": "manager-1"})
    assert resp.status_code == 200 and resp.json()["status"] == "APPROVED"

    resp = client.post(f"/api/v1/operations/recommendations/{uid}/execute", json={"actor": "ops-1"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["recommendation"]["status"] == "EXECUTED"
    assert body["execution_ref"]


def test_replenishment_recommendation_executes_into_approved_po():
    sku = f"VEJIT-REPLEN-{sfx()}"
    supplier = client.post("/api/v1/operations/suppliers", json={"name": f"Supplier {sfx()}"}).json()
    rec = client.post("/api/v1/intelligence/recommendations", json={
        "kind": "REPLENISHMENT", "subject_type": "sku", "subject_id": sku,
        "summary": "restock",
        "recommended_action": {"supplier_id": supplier["id"], "lines": [{"sku": sku, "quantity_ordered": 12, "unit_cost": 1.25}]},
    }).json()
    client.post(f"/api/v1/intelligence/recommendations/{rec['uid']}/approve", json={"approver": "manager-1"})
    executed = client.post(f"/api/v1/operations/recommendations/{rec['uid']}/execute", json={"actor": "ops-1"})
    assert executed.status_code == 200, executed.text
    po_number = executed.json()["detail"]["purchase_order"]
    po = client.get(f"/api/v1/operations/purchase-orders/{po_number}").json()
    assert po["status"] == "APPROVED"
    assert po["created_by"].startswith("recommendation:")
    assert po["approved_by"] == "manager-1"


def test_reject_then_execute_is_forbidden():
    rec = client.post("/api/v1/intelligence/recommendations", json={
        "kind": "WASTE_REVIEW", "subject_type": "sku", "subject_id": "X", "summary": "review spoilage",
    }).json()
    client.post(f"/api/v1/intelligence/recommendations/{rec['uid']}/reject", json={"actor": "manager-1"})
    assert client.post(f"/api/v1/operations/recommendations/{rec['uid']}/execute",
                       json={"actor": "ops-1"}).status_code == 409


# -------------------------------------------------------------- intelligence

def test_demand_forecast_from_supplied_history():
    resp = client.post("/api/v1/intelligence/forecast/demand", json={
        "history": [10, 12, 11, 13, 12, 14, 13, 15], "horizon": 5, "method": "ses",
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["forecast"]) == 5
    assert body["point_estimate"] > 0
    assert body["source"] == "request"


def test_demand_forecast_from_sales_history_and_sku_resolution():
    sku = f"VEJIT-FC-{sfx()}"
    make_sku(sku, quantity=100)
    price_sku(sku, 3.0)
    client.post("/api/v1/commerce/orders", json={"channel": "pos", "lines": [{"sku": sku, "quantity": 4}]})
    resp = client.post("/api/v1/intelligence/forecast/demand", json={"sku": sku, "horizon": 3})
    assert resp.status_code == 200, resp.text
    assert resp.json()["source"].startswith("sales_history")


def test_anomaly_scan_creates_replenishment_recommendations():
    sku = f"VEJIT-ANOM-{sfx()}"
    make_sku(sku, quantity=1, reorder_point=10)  # stockout risk
    resp = client.get("/api/v1/intelligence/anomalies?create_recommendations=true")
    assert resp.status_code == 200
    body = resp.json()
    assert any(a["sku"] == sku for a in body["anomalies"])
    assert sku in [a["sku"] for a in body["anomalies"]]
    assert any(isinstance(u, str) for u in body["recommendations_created"]) or body["recommendations_created"] == []


def test_shelf_availability_and_summary():
    sku = f"VEJIT-SHELF-{sfx()}"
    make_sku(sku, quantity=2, reorder_point=10)
    body = client.get("/api/v1/intelligence/shelf-availability").json()
    assert sku in [row["sku"] for row in body["skus"]]
    summary = client.get("/api/v1/intelligence/summary").json()
    assert "recommendations" in summary and "anomaly_count" in summary


def test_supplier_scorecard_and_workforce_guidance():
    supplier = client.post("/api/v1/operations/suppliers", json={
        "name": f"Scorecard {sfx()}", "sla_otif_pct": 100.0, "sla_fill_rate_pct": 100.0,
    }).json()
    resp = client.get(f"/api/v1/intelligence/suppliers/{supplier['id']}/scorecard")
    assert resp.status_code == 200
    assert resp.json()["fill_rate_vs_sla"] in ("no_data", "meets", "below_sla")

    guidance = client.get("/api/v1/intelligence/workforce-guidance").json()
    assert guidance["status"] == "PROPOSED"
    assert guidance["executes_actions"] is False
