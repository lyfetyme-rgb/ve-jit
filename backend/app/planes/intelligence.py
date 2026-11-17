"""Intelligence plane domain services.

Intelligence produces recommendations. It never mutates stock, prices, orders
or purchase orders. Everything in this module is either a pure computation or
a read against the trusted layer, plus optional creation of PROPOSED
recommendation rows via the governance module.

Forecasting is deterministic and offline: simple exponential smoothing (SES)
or a simple moving average (SMA). No external model is required and no
network calls are made.
"""

import json
import math
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy.orm import Session

from app.db.models import InventoryItem
from app.planes.governance import create_recommendation
from app.planes.models import CommerceOrder, CommerceOrderLine, Recommendation, Supplier, PurchaseOrder, PurchaseOrderLine, WasteRecord


# ---------------------------------------------------------------- forecast

def forecast_demand(
    history: List[float],
    horizon: int = 7,
    method: str = "ses",
    alpha: float = 0.4,
    window: int = 7,
) -> dict:
    if not history:
        raise ValueError("history must contain at least one observation")
    if horizon < 1 or horizon > 365:
        raise ValueError("horizon must be between 1 and 365")
    cleaned = [float(v) for v in history]

    method = (method or "ses").lower()
    if method == "ses":
        alpha = max(0.05, min(0.95, float(alpha)))
        level = cleaned[0]
        residuals = []
        for actual in cleaned[1:]:
            residuals.append(actual - level)
            level = alpha * actual + (1 - alpha) * level
        point = level
    elif method == "sma":
        window = max(1, int(window))
        tail = cleaned[-window:]
        point = sum(tail) / len(tail)
        residuals = [v - point for v in cleaned]
    else:
        raise ValueError("method must be 'ses' or 'sma'")

    variance = sum(r * r for r in residuals) / len(residuals) if residuals else 0.0
    error_std = round(math.sqrt(variance), 4)
    forecast = [{"step": i + 1, "value": round(point, 4)} for i in range(horizon)]
    confidence = round(max(0.0, min(1.0, 1.0 - (error_std / (abs(point) + 1e-6)))), 4)
    return {
        "method": method,
        "history_points": len(cleaned),
        "horizon": horizon,
        "forecast": forecast,
        "point_estimate": round(point, 4),
        "error_stddev": error_std,
        "confidence": confidence,
        "note": "Deterministic offline forecast; calibrate against measured outcomes before acting.",
    }


def sales_velocity(db: Session, sku: str, days: int = 28) -> float:
    """Units sold per day over the trailing `days` from posted commerce orders."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    rows = (
        db.query(CommerceOrderLine.quantity)
        .join(CommerceOrder, CommerceOrder.id == CommerceOrderLine.order_id)
        .filter(CommerceOrderLine.sku == sku)
        .filter(CommerceOrder.status != "CANCELLED")
        .filter(CommerceOrder.created_at >= cutoff)
        .all()
    )
    units = sum(int(r[0]) for r in rows)
    return units / float(days)


def sales_history(db: Session, sku: str, days: int = 28) -> List[float]:
    """Daily units for the trailing window, zero-filled."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    rows = (
        db.query(CommerceOrder.created_at, CommerceOrderLine.quantity)
        .join(CommerceOrderLine, CommerceOrderLine.order_id == CommerceOrder.id)
        .filter(CommerceOrderLine.sku == sku)
        .filter(CommerceOrder.status != "CANCELLED")
        .filter(CommerceOrder.created_at >= cutoff)
        .all()
    )
    buckets: dict[str, int] = {}
    for created_at, quantity in rows:
        day = _aware(created_at).date().isoformat()
        buckets[day] = buckets.get(day, 0) + int(quantity)
    today = datetime.now(timezone.utc).date()
    history = []
    for offset in range(days - 1, -1, -1):
        day = (today - timedelta(days=offset)).isoformat()
        history.append(float(buckets.get(day, 0)))
    return history


# --------------------------------------------------------------- anomalies

def detect_anomalies(db: Session, store_id: str, create_recommendations: bool = False, limit: int = 100) -> dict:
    items = db.query(InventoryItem).order_by(InventoryItem.sku.asc()).all()
    anomalies = []
    for item in items:
        velocity = sales_velocity(db, item.sku)
        days_of_supply = round(item.quantity / velocity, 2) if velocity > 0 else None
        if item.quantity <= 0:
            anomalies.append(_anomaly("STOCKOUT", "critical", item, velocity, days_of_supply,
                                      f"{item.sku} is out of stock"))
        elif item.quantity <= item.reorder_point:
            anomalies.append(_anomaly("STOCKOUT_RISK", "warning", item, velocity, days_of_supply,
                                      f"{item.sku} at {item.quantity} on hand, reorder point {item.reorder_point}"))
        elif item.reorder_point > 0 and item.quantity >= 6 * item.reorder_point and velocity == 0:
            anomalies.append(_anomaly("OVERSTOCK_DEAD_STOCK", "watch", item, velocity, days_of_supply,
                                      f"{item.sku} has {item.quantity} on hand with zero sales velocity (dead stock)"))
        if len(anomalies) >= limit:
            break

    created_uids = []
    if create_recommendations:
        for anomaly in anomalies:
            if anomaly["type"] in ("STOCKOUT", "STOCKOUT_RISK"):
                existing = (
                    db.query(Recommendation)
                    .filter(Recommendation.kind == "REPLENISHMENT")
                    .filter(Recommendation.subject_id == anomaly["sku"])
                    .filter(Recommendation.status.in_(["PROPOSED", "APPROVED"]))
                    .first()
                )
                if existing:
                    continue
                action = {
                    "sku": anomaly["sku"],
                    "suggested_order_quantity": max(anomaly["reorder_point"] * 2 - anomaly["on_hand"], anomaly["reorder_point"]),
                    "reason": anomaly["type"],
                }
                rec = create_recommendation(
                    db,
                    kind="REPLENISHMENT",
                    subject_type="sku",
                    subject_id=anomaly["sku"],
                    summary=anomaly["detail"],
                    rationale=f"days_of_supply={anomaly['days_of_supply']}; velocity={anomaly['daily_velocity']}/day",
                    confidence=0.7 if anomaly["type"] == "STOCKOUT" else 0.6,
                    recommended_action=json.dumps(action),
                )
                created_uids.append(rec.uid)

    return {
        "store_id": store_id,
        "scanned": len(items),
        "anomalies": anomalies,
        "recommendations_created": created_uids,
        "note": "Intelligence output only; Operations must approve and execute any resulting action.",
    }


def _anomaly(kind, severity, item, velocity, days_of_supply, detail) -> dict:
    return {
        "type": kind,
        "severity": severity,
        "sku": item.sku,
        "on_hand": item.quantity,
        "reorder_point": item.reorder_point,
        "daily_velocity": round(velocity, 4),
        "days_of_supply": days_of_supply,
        "detail": detail,
    }


# -------------------------------------------------------- shelf availability

def shelf_availability(db: Session, store_id: str) -> dict:
    items = db.query(InventoryItem).order_by(InventoryItem.sku.asc()).all()
    rows = []
    band_counts = {"healthy": 0, "at_risk": 0, "critical": 0, "stockout": 0}
    for item in items:
        velocity = sales_velocity(db, item.sku)
        days_of_supply = round(item.quantity / velocity, 2) if velocity > 0 else None
        if item.quantity <= 0:
            band = "stockout"
        elif days_of_supply is not None and days_of_supply <= 2:
            band = "critical"
        elif item.quantity <= item.reorder_point or (days_of_supply is not None and days_of_supply <= 7):
            band = "at_risk"
        else:
            band = "healthy"
        band_counts[band] += 1
        rows.append({
            "sku": item.sku,
            "on_hand": item.quantity,
            "reorder_point": item.reorder_point,
            "daily_velocity": round(velocity, 4),
            "days_of_supply": days_of_supply,
            "availability_band": band,
        })
    total = len(rows) or 1
    return {
        "store_id": store_id,
        "skus": rows,
        "bands": band_counts,
        "healthy_pct": round(100.0 * band_counts["healthy"] / total, 2),
        "note": "Availability is estimated from on-hand, reorder point and trailing 28-day sales velocity.",
    }


# ----------------------------------------------------------------- waste

def waste_summary(db: Session, store_id: str, days: int = 30) -> dict:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    rows = db.query(WasteRecord).filter(WasteRecord.created_at >= cutoff).all()
    by_reason: dict[str, dict] = {}
    by_sku: dict[str, dict] = {}
    total_cost = 0.0
    total_units = 0
    for row in rows:
        if row.store_id != store_id:
            continue
        total_cost += float(row.estimated_cost)
        total_units += int(row.quantity)
        r = by_reason.setdefault(row.reason, {"units": 0, "estimated_cost": 0.0})
        r["units"] += row.quantity
        r["estimated_cost"] = round(r["estimated_cost"] + float(row.estimated_cost), 2)
        s = by_sku.setdefault(row.sku, {"units": 0, "estimated_cost": 0.0})
        s["units"] += row.quantity
        s["estimated_cost"] = round(s["estimated_cost"] + float(row.estimated_cost), 2)

    top_skus = sorted(
        ({"sku": sku, **data} for sku, data in by_sku.items()),
        key=lambda r: (r["estimated_cost"], r["units"]),
        reverse=True,
    )[:10]

    dead_stock = []
    for item in db.query(InventoryItem).all():
        if item.quantity > 0 and sales_velocity(db, item.sku) == 0 and item.unit_cost > 0:
            dead_stock.append({
                "sku": item.sku,
                "on_hand": item.quantity,
                "carrying_cost": round(item.quantity * item.unit_cost, 2),
            })
    dead_stock.sort(key=lambda r: r["carrying_cost"], reverse=True)

    return {
        "store_id": store_id,
        "window_days": days,
        "total_units_lost": total_units,
        "total_estimated_cost": round(total_cost, 2),
        "by_reason": by_reason,
        "top_waste_skus": top_skus,
        "dead_stock_watch": dead_stock[:10],
        "note": "Intelligence output only; Operations records waste events and any resulting actions.",
    }


# ---------------------------------------------------------------- suppliers

def supplier_scorecard(db: Session, supplier_id: int) -> dict:
    supplier = db.query(Supplier).filter(Supplier.id == supplier_id).first()
    if supplier is None:
        raise ValueError(f"Supplier {supplier_id} not found")

    pos = db.query(PurchaseOrder).filter(PurchaseOrder.supplier_id == supplier.id).all()
    ordered = 0
    received = 0
    on_time = 0
    timed = 0
    for po in pos:
        lines = db.query(PurchaseOrderLine).filter(PurchaseOrderLine.po_id == po.id).all()
        ordered += sum(l.quantity_ordered for l in lines)
        received += sum(l.quantity_received for l in lines)
        if po.expected_at is not None and po.received_at is not None:
            timed += 1
            if _aware(po.received_at) <= _aware(po.expected_at):
                on_time += 1

    fill_rate = round(100.0 * received / ordered, 2) if ordered else None
    on_time_pct = round(100.0 * on_time / timed, 2) if timed else None

    def verdict(actual, target):
        if actual is None:
            return "no_data"
        return "meets" if actual >= target else "below_sla"

    return {
        "supplier": {
            "id": supplier.id,
            "name": supplier.name,
            "payment_terms": supplier.payment_terms,
            "lead_time_days": supplier.lead_time_days,
            "sla_otif_pct": supplier.sla_otif_pct,
            "sla_fill_rate_pct": supplier.sla_fill_rate_pct,
        },
        "purchase_orders": len(pos),
        "units_ordered": ordered,
        "units_received": received,
        "fill_rate_pct": fill_rate,
        "fill_rate_vs_sla": verdict(fill_rate, supplier.sla_fill_rate_pct),
        "on_time_pct": on_time_pct,
        "on_time_vs_sla": verdict(on_time_pct, supplier.sla_otif_pct),
        "note": "Scorecard derived from recorded purchase orders and receipts; no inference beyond the records.",
    }


def _aware(value):
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value
