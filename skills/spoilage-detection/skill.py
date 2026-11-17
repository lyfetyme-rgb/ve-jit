"""spoilage-detection skill — flags perishables at risk and dead stock.

Input:  {"items": [{"sku": str, "quantity": int, "unit_cost": float,
                     "days_since_stock": int?, "daily_velocity": float?,
                     "expiry_days_remaining": int?}]}
Output: {"ok": true, "output": {"at_risk": [...], "dead_stock": [...],
                                "total_value_at_risk": float}}
"""

import json
import sys


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return emit(False, errors=["stdin is not valid JSON"])

    items = payload.get("items")
    if not isinstance(items, list):
        return emit(False, errors=["items must be an array"])

    at_risk = []
    dead_stock = []
    total_value = 0.0
    for item in items:
        sku = item.get("sku")
        if not sku:
            return emit(False, errors=["every item needs a sku"])
        qty = int(item.get("quantity", 0))
        cost = float(item.get("unit_cost", 0.0))
        velocity = float(item.get("daily_velocity", 0.0) or 0.0)
        days_left = item.get("expiry_days_remaining")
        value = round(qty * cost, 2)

        if days_left is not None and int(days_left) <= 3 and qty > 0:
            total_value += value
            at_risk.append({"sku": sku, "quantity": qty, "expiry_days_remaining": int(days_left), "value_at_risk": value})
        elif velocity == 0.0 and qty > 0 and cost > 0.0:
            total_value += value
            dead_stock.append({"sku": sku, "quantity": qty, "carrying_cost": value})

    return emit(True, output={
        "at_risk": at_risk,
        "dead_stock": dead_stock,
        "total_value_at_risk": round(total_value, 2),
    }, confidence=0.8)


def emit(ok: bool, output=None, confidence=None, errors=None) -> int:
    body = {"ok": ok}
    if output is not None:
        body["output"] = output
    if confidence is not None:
        body["confidence"] = confidence
    if errors:
        body["errors"] = errors
    sys.stdout.write(json.dumps(body))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
