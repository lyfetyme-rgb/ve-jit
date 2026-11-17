"""supplier-analysis skill — fill rate and on-time vs contracted SLA.

Input:  {"supplier": {"name": str, "sla_otif_pct": float, "sla_fill_rate_pct": float},
         "pos": [{"units_ordered": int, "units_received": int, "on_time": bool}]}
Output: {"ok": true, "output": {"fill_rate_pct": float|null, "on_time_pct": float|null,
                                "fill_rate_vs_sla": str, "on_time_vs_sla": str}}
"""

import json
import sys


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return emit(False, errors=["stdin is not valid JSON"])

    supplier = payload.get("supplier") or {}
    pos = payload.get("pos")
    if not isinstance(pos, list):
        return emit(False, errors=["pos must be an array"])
    sla_fill = float(supplier.get("sla_fill_rate_pct", 98.0))
    sla_otif = float(supplier.get("sla_otif_pct", 95.0))

    ordered = sum(int(p.get("units_ordered", 0)) for p in pos)
    received = sum(int(p.get("units_received", 0)) for p in pos)
    timed = sum(1 for p in pos if p.get("on_time") is not None)
    on_time = sum(1 for p in pos if p.get("on_time") is True)

    fill = round(100.0 * received / ordered, 2) if ordered else None
    oti = round(100.0 * on_time / timed, 2) if timed else None

    def verdict(actual, target):
        if actual is None:
            return "no_data"
        return "meets" if actual >= target else "below_sla"

    return emit(True, output={
        "supplier": supplier.get("name"),
        "purchase_orders": len(pos),
        "units_ordered": ordered,
        "units_received": received,
        "fill_rate_pct": fill,
        "on_time_pct": oti,
        "fill_rate_vs_sla": verdict(fill, sla_fill),
        "on_time_vs_sla": verdict(oti, sla_otif),
    }, confidence=0.9 if pos else 0.3)


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
