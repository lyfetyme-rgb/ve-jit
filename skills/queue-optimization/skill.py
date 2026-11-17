"""queue-optimization skill — lanes-to-open guidance per lane snapshot.

Input:  {"lanes": [{"lane_id": str, "customers_in_line": int,
                     "average_wait_time": float, "cashier_speed": float}],
         "target_wait_seconds": float?}
Output: {"ok": true, "output": {"recommended_open_lanes": int, "reasoning": [...]}}
"""

import json
import sys


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return emit(False, errors=["stdin is not valid JSON"])

    lanes = payload.get("lanes")
    if not isinstance(lanes, list) or not lanes:
        return emit(False, errors=["lanes must be a non-empty array"])
    target = float(payload.get("target_wait_seconds", 180.0))

    open_now = 0
    reasoning = []
    for lane in lanes:
        lane_id = lane.get("lane_id", "unknown")
        wait = float(lane.get("average_wait_time", 0.0))
        line = int(lane.get("customers_in_line", 0))
        speed = float(lane.get("cashier_speed", 20.0)) or 20.0
        if wait > target * 1.5 and line >= 6:
            open_now += 1
            reasoning.append(f"{lane_id}: wait {wait:.0f}s over target {target:.0f}s with {line} waiting and cashier speed {speed:.0f} items/min -> open another lane")
        else:
            reasoning.append(f"{lane_id}: wait {wait:.0f}s acceptable (target {target:.0f}s)")

    return emit(True, output={
        "recommended_open_lanes": open_now,
        "reasoning": reasoning,
        "target_wait_seconds": target,
    }, confidence=0.75)


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
