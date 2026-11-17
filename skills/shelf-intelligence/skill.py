"""shelf-intelligence skill — days-of-supply banding per SKU.

Input:  {"items": [{"sku": str, "on_hand": int, "reorder_point": int, "daily_velocity": float}]}
Output: {"ok": true, "output": {"skus": [...], "bands": {...}}}
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

    rows = []
    bands = {"healthy": 0, "at_risk": 0, "critical": 0, "stockout": 0}
    for item in items:
        sku = item.get("sku")
        if not sku:
            return emit(False, errors=["every item needs a sku"])
        on_hand = int(item.get("on_hand", 0))
        reorder = int(item.get("reorder_point", 0))
        velocity = float(item.get("daily_velocity", 0.0) or 0.0)
        days = round(on_hand / velocity, 2) if velocity > 0 else None

        if on_hand <= 0:
            band = "stockout"
        elif days is not None and days <= 2:
            band = "critical"
        elif on_hand <= reorder or (days is not None and days <= 7):
            band = "at_risk"
        else:
            band = "healthy"
        bands[band] += 1
        rows.append({"sku": sku, "on_hand": on_hand, "reorder_point": reorder,
                     "daily_velocity": round(velocity, 4), "days_of_supply": days,
                     "availability_band": band})

    return emit(True, output={"skus": rows, "bands": bands}, confidence=0.85)


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
