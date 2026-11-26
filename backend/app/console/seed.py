"""Config-driven, idempotent retailer loading for the Store Console.

No retailer names are hardcoded. The default file contains three generic
archetypes (grocery, hardware, home improvement). Point ``RETAILER_CONFIG`` at
a copy to load your own companies; ``tools/load_retailers.py`` does the same
from the command line.

Catalog format (see ``config/retailers.demo.json``)::

    {
      "retailers": [
        {
          "tenant_id": "my_customer", "name": "My Customer",
          "vertical": "grocery", "description": "...", "sku_prefix": "MC-",
          "stores": [{"store_id": "MC-001", "name": "...", "location": "...",
                      "architecture": "amd64", "channel": "store"}],
          "catalog": [{"sku": "MC-1", "name": "...", "price": 1.0,
                       "quantity": 10, "reorder_point": 3}]
        }
      ]
    }
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.console.models import ConsoleStore, ConsoleTenant
from app.db.models import InventoryItem
from app.planes.commerce import CommerceOrder, CommerceOrderLine
from app.planes.models import (
    InventoryLedgerEntry,
    ProductPrice,
    Recommendation,
    Supplier,
)

DEFAULT_CONFIG_PATH = (
    Path(__file__).resolve().parents[3] / "config" / "retailers.demo.json"
)


def config_path(path: Optional[str | Path] = None) -> Path:
    """Resolve the active retailer config path (explicit > env > default)."""
    if path:
        return Path(path)
    env = (os.environ.get("RETAILER_CONFIG") or "").strip()
    return Path(env) if env else DEFAULT_CONFIG_PATH


def load_retailer_config(path: Optional[str | Path] = None) -> dict[str, Any]:
    """Read and validate a retailer catalog file."""
    target = config_path(path)
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"Retailer config not found: {target}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in retailer config {target}: {exc}") from exc

    if not isinstance(data, dict) or not isinstance(data.get("retailers"), list):
        raise ValueError("Retailer config must be an object with a 'retailers' array")

    for index, retailer in enumerate(data["retailers"]):
        for field in ("tenant_id", "name", "vertical", "sku_prefix", "stores", "catalog"):
            if field not in retailer:
                raise ValueError(f"retailers[{index}] is missing required field {field!r}")
        if not isinstance(retailer["stores"], list) or not isinstance(retailer["catalog"], list):
            raise ValueError(f"retailers[{index}].stores and .catalog must be arrays")
        for store in retailer["stores"]:
            for field in ("store_id", "name"):
                if field not in store:
                    raise ValueError(f"retailers[{index}] store is missing {field!r}")
        for item in retailer["catalog"]:
            for field in ("sku", "name", "price", "quantity", "reorder_point"):
                if field not in item:
                    raise ValueError(f"retailers[{index}] catalog item is missing {field!r}")
    return data


def get_sku_prefix(db: Session, tenant_id: str) -> str:
    """SKU namespace for a tenant, read from the loaded tenant registry."""
    tenant = db.query(ConsoleTenant).filter(ConsoleTenant.tenant_id == tenant_id).first()
    if tenant is not None and tenant.sku_prefix:
        return str(tenant.sku_prefix)
    return tenant_id.upper() + "-"


def seed_retailers(db: Session, config: Optional[dict[str, Any] | str | Path] = None) -> dict:
    """Create tenants, stores and domain rows from config. Safe to repeat."""
    if config is None or isinstance(config, (str, Path)):
        catalog = load_retailer_config(config)
    else:
        catalog = config

    created = {"tenants": 0, "stores": 0, "skus": 0, "orders": 0, "recommendations": 0}

    for retailer in catalog["retailers"]:
        tenant = (
            db.query(ConsoleTenant)
            .filter(ConsoleTenant.tenant_id == retailer["tenant_id"])
            .first()
        )
        if tenant is None:
            tenant = ConsoleTenant(
                tenant_id=retailer["tenant_id"],
                name=retailer["name"],
                vertical=retailer["vertical"],
                description=retailer.get("description"),
                sku_prefix=retailer["sku_prefix"],
            )
            db.add(tenant)
            created["tenants"] += 1
        else:
            # Config is authoritative for descriptive fields and namespace.
            tenant.name = retailer["name"]
            tenant.vertical = retailer["vertical"]
            tenant.description = retailer.get("description")
            tenant.sku_prefix = retailer["sku_prefix"]

        for store_spec in retailer["stores"]:
            store_id = store_spec["store_id"]
            store = db.query(ConsoleStore).filter(ConsoleStore.store_id == store_id).first()
            if store is None:
                db.add(ConsoleStore(
                    store_id=store_id,
                    tenant_id=retailer["tenant_id"],
                    name=store_spec["name"],
                    location=store_spec.get("location"),
                    architecture=store_spec.get("architecture", "amd64"),
                    channel=store_spec.get("channel", "store"),
                ))
                created["stores"] += 1
            else:
                store.name = store_spec["name"]
                store.location = store_spec.get("location")
                store.architecture = store_spec.get("architecture", "amd64")

            supplier_id = _ensure_supplier(db, retailer["tenant_id"])
            for item in retailer["catalog"]:
                sku = item["sku"]
                price = float(item["price"])
                if db.query(InventoryItem).filter(InventoryItem.sku == sku).first() is None:
                    db.add(InventoryItem(
                        sku=sku, name=item["name"],
                        quantity=int(item["quantity"]),
                        reorder_point=int(item["reorder_point"]),
                        unit_cost=round(price * 0.65, 4), version=1,
                    ))
                    db.add(InventoryLedgerEntry(
                        sku=sku, store_id=store_id, delta=int(item["quantity"]),
                        reason="RECEIPT", actor="console-seed",
                        ref=f"seed:{retailer['tenant_id']}",
                    ))
                    created["skus"] += 1
                if (
                    db.query(ProductPrice)
                    .filter(ProductPrice.sku == sku, ProductPrice.store_id == store_id)
                    .first()
                    is None
                ):
                    db.add(ProductPrice(
                        sku=sku, store_id=store_id, unit_price=price,
                        valid_from=datetime.now(timezone.utc), active=True,
                    ))

            order_ref = f"SEED-{store_id}"
            if db.query(CommerceOrder).filter(CommerceOrder.order_ref == order_ref).first() is None:
                sample = retailer["catalog"][:2]
                total = round(sum(float(i["price"]) for i in sample), 2)
                order = CommerceOrder(
                    order_ref=order_ref,
                    client_order_id=f"seed:{store_id}",
                    channel="pos", store_id=store_id, register_id="lane-seed",
                    payment_method="card", status="PAID",
                    subtotal=total, discount_total=0.0, total=total, points_earned=0,
                )
                db.add(order)
                db.flush()
                for item in sample:
                    db.add(CommerceOrderLine(
                        order_id=order.id, sku=item["sku"], name=item["name"],
                        quantity=1, unit_price=float(item["price"]),
                        discount=0.0, line_total=float(item["price"]),
                    ))
                created["orders"] += 1

            first = retailer["catalog"][0]
            action = json.dumps({
                "supplier_id": supplier_id,
                "lines": [{
                    "sku": first["sku"], "quantity_ordered": 12,
                    "unit_cost": round(float(first["price"]) * 0.65, 4),
                }],
            })
            rec_uid = f"rec_console_{store_id}"
            if db.query(Recommendation).filter(Recommendation.uid == rec_uid).first() is None:
                db.add(Recommendation(
                    uid=rec_uid, plane="intelligence", kind="REPLENISHMENT",
                    subject_type="sku", subject_id=first["sku"],
                    tenant_id=retailer["tenant_id"], store_id=store_id,
                    summary=f"Replenish {first['sku']} at {store_spec['name']}",
                    rationale=f"On-hand is at or near reorder point for {store_spec['name']}.",
                    confidence=0.82, recommended_action=action,
                    status="PROPOSED", created_by="console-seed", version=1,
                ))
                created["recommendations"] += 1

    db.commit()
    return created


# Backwards-compatible name used by main.py and the existing tests.
seed_demo = seed_retailers


def _ensure_supplier(db: Session, tenant_id: str) -> int:
    name = f"{tenant_id} Supply Co"
    row = db.query(Supplier).filter(Supplier.name == name).first()
    if row is None:
        row = Supplier(
            name=name, payment_terms="NET30", lead_time_days=2,
            sla_otif_pct=97.0, sla_fill_rate_pct=98.0,
        )
        db.add(row)
        db.flush()
    return row.id
