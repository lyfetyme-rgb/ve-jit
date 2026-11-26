"""Idempotent demo seeding for Food Lion / Ace Hardware / Lowe's."""

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.console.models import ConsoleStore, ConsoleTenant
from app.db.models import InventoryItem
from app.planes.models import InventoryLedgerEntry
from app.planes.commerce import CommerceOrder, CommerceOrderLine
from app.planes.governance import new_uid
from app.planes.models import CommerceOrder, CommerceOrderLine, ProductPrice, Recommendation, Supplier, InventoryLedgerEntry

# SKU namespaces used by each demo tenant. Kept explicit rather than derived
# from the tenant id so short codes (FL-, LOW-) remain stable and readable.
TENANT_SKU_PREFIXES = {
    "foodlion": "FL-",
    "acehardware": "ACE-",
    "lowes": "LOW-",
}


def sku_prefix(tenant_id: str) -> str:
    """Return the SKU namespace for a tenant, with a sensible generic fallback."""
    return TENANT_SKU_PREFIXES.get(tenant_id, tenant_id.upper() + "-")


DEMO_TENANTS = [
    {
        "tenant_id": "foodlion",
        "name": "Food Lion",
        "vertical": "grocery",
        "description": "Grocery / cold-chain retail demo. Edge node amd64, back-office store 042.",
        "stores": [("FL-VA-042", "Food Lion — Richmond VA", "amd64", "Richmond, VA")],
        "catalog": [
            ("FL-MILK-1G", "Whole Milk 1 Gallon", 3.79, 48, 12),
            ("FL-EGGS-12", "Large Eggs 12 ct", 3.29, 36, 12),
            ("FL-BREAD-WH", "White Bread 20 oz", 2.49, 60, 18),
            ("FL-CHICK-BR", "Chicken Breast lb", 6.99, 22, 8),
        ],
    },
    {
        "tenant_id": "acehardware",
        "name": "Ace Hardware",
        "vertical": "hardware",
        "description": "Hardware co-op demo. Compact edge gateway arm64.",
        "stores": [("ACE-NC-001", "Ace Hardware — Raleigh NC", "arm64", "Raleigh, NC")],
        "catalog": [
            ("ACE-PAINT-GAL", "Interior Paint Gallon", 38.99, 24, 6),
            ("ACE-DRILL-20V", "20V Cordless Drill Kit", 129.0, 14, 4),
            ("ACE-SCREWS-100", "Deck Screws 100 ct", 12.49, 40, 10),
            ("ACE-BRUSH-9", "9-inch Paint Roller Kit", 9.99, 28, 8),
        ],
    },
    {
        "tenant_id": "lowes",
        "name": "Lowe's",
        "vertical": "home_improvement",
        "description": "Home improvement / distribution demo. Back-office amd64.",
        "stores": [("LOW-NC-DC1", "Lowe's — Charlotte DC", "amd64", "Charlotte, NC")],
        "catalog": [
            ("LOW-LUMBER-2X4", "2x4x8 Stud Lumber", 4.28, 220, 60),
            ("LOW-FASTENER-50", "Construction Fasteners 50 lb", 32.5, 30, 8),
            ("LOW-PAINT-5G", "Exterior Paint 5 Gallon", 148.0, 18, 4),
            ("LOW-APPL-DW", "Dishwasher Stainless", 549.0, 9, 3),
        ],
    },
]


def seed_demo(db: Session) -> dict:
    """Create demo tenants, stores and domain rows. Safe to call repeatedly."""
    created = {"tenants": 0, "stores": 0, "skus": 0, "orders": 0, "recommendations": 0}

    for demo in DEMO_TENANTS:
        tenant = db.query(ConsoleTenant).filter(ConsoleTenant.tenant_id == demo["tenant_id"]).first()
        if tenant is None:
            tenant = ConsoleTenant(
                tenant_id=demo["tenant_id"], name=demo["name"],
                vertical=demo["vertical"], description=demo["description"],
            )
            db.add(tenant)
            created["tenants"] += 1

        for store_id, name, arch, location in demo["stores"]:
            store = db.query(ConsoleStore).filter(ConsoleStore.store_id == store_id).first()
            if store is None:
                db.add(ConsoleStore(
                    store_id=store_id, tenant_id=demo["tenant_id"], name=name,
                    location=location, architecture=arch, channel="store",
                ))
                created["stores"] += 1

            supplier_id = _ensure_supplier(db, demo["tenant_id"])
            for sku, item_name, price, qty, reorder in demo["catalog"]:
                if db.query(InventoryItem).filter(InventoryItem.sku == sku).first() is None:
                    db.add(InventoryItem(sku=sku, name=item_name, quantity=qty, reorder_point=reorder,
                                         unit_cost=round(price * 0.65, 4), version=1))
                    db.add(InventoryLedgerEntry(sku=sku, store_id=store_id, delta=qty,
                                                reason="RECEIPT", actor="console-seed", ref=f"seed:{demo['tenant_id']}"))
                    created["skus"] += 1
                if db.query(ProductPrice).filter(ProductPrice.sku == sku, ProductPrice.store_id == store_id).first() is None:
                    db.add(ProductPrice(sku=sku, store_id=store_id, unit_price=price,
                                        valid_from=datetime.now(timezone.utc), active=True))

            if db.query(CommerceOrder).filter(CommerceOrder.order_ref == f"SEED-{store_id}").first() is None:
                order = CommerceOrder(
                    order_ref=f"SEED-{store_id}", client_order_id=f"seed:{store_id}",
                    channel="pos", store_id=store_id, register_id="lane-seed",
                    payment_method="card", status="PAID",
                    subtotal=round(sum(p for _, _, p, _, _ in demo["catalog"][:2]), 2),
                    discount_total=0.0,
                    total=round(sum(p for _, _, p, _, _ in demo["catalog"][:2]), 2),
                    points_earned=0,
                )
                db.add(order)
                db.flush()
                for sku, item_name, price, _, _ in demo["catalog"][:2]:
                    db.add(CommerceOrderLine(order_id=order.id, sku=sku, name=item_name,
                                             quantity=1, unit_price=price, discount=0.0, line_total=price))
                created["orders"] += 1

            sku_low = demo["catalog"][0][0]
            action = json.dumps({"supplier_id": supplier_id, "lines": [{"sku": sku_low, "quantity_ordered": 12, "unit_cost": round(demo["catalog"][0][2] * 0.65, 4)}]})
            rec_uid = f"rec_console_{store_id}"
            if db.query(Recommendation).filter(Recommendation.uid == rec_uid).first() is None:
                db.add(Recommendation(
                    uid=rec_uid, plane="intelligence", kind="REPLENISHMENT",
                    subject_type="sku", subject_id=sku_low,
                    tenant_id=demo["tenant_id"], store_id=store_id,
                    summary=f"Replenish {sku_low} at {name}",
                    rationale=f"On-hand is at or near reorder point for {name}.",
                    confidence=0.82, recommended_action=action,
                    status="PROPOSED", created_by="console-seed", version=1,
                ))
                created["recommendations"] += 1

    db.commit()
    return created


def _ensure_supplier(db: Session, tenant_id: str) -> int:
    name = f"{tenant_id.title()} Supply Co"
    row = db.query(Supplier).filter(Supplier.name == name).first()
    if row is None:
        row = Supplier(name=name, payment_terms="NET30", lead_time_days=2,
                       sla_otif_pct=97.0, sla_fill_rate_pct=98.0)
        db.add(row)
        db.flush()
    return row.id
