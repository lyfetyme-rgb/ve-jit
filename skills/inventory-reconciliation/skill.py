"""inventory-reconciliation skill — variance between counted and projected units.

Input:  {"lines": [{"sku": str, "counted": int, "expected": int}, ...]}
Output: {"ok": true, "output": {"variances": [...], "total_abs_variance": int, "accuracy_pct": float}}
"""

import json
import sys


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return emit(False, errors=["stdin is not valid JSON"])

    lines = payload.get("lines")
    if not isinstance(lines, list) or not lines:
        return emit(False, errors=["lines must be a non-empty array"])

    variances = []
    abs_total = 0
    counted_total = 0
    expected_total = 0
    for line in lines:
        sku = line.get("sku")
        if not sku:
            return emit(False, errors=["every line needs a sku"])
        counted = int(line["counted"])
        expected = int(line["expected"])
        variance = counted - expected
        abs_total += abs(variance)
        counted_total += counted
        expected_total += expected
        variances.append({"sku": sku, "counted": counted, "expected": expected, "variance": variance})

    max_reference = max(counted_total, expected_total, 1)
    accuracy = round(max(0.0, (max_reference - abs_total) / max_reference) * 100.0, 2)

    return emit(True, output={
        "variances": variances,
        "total_abs_variance": abs_total,
        "accuracy_pct": accuracy,
        "recount_required": [v["sku"] for v in variances if v["variance"] != 0],
    }, confidence=0.9)


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
