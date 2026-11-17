"""demand-forecasting skill — deterministic SES/SMA forecast over a history series.

Contract: reads a JSON payload from stdin, writes a JSON result to stdout.
Never touches the operational database.

Input:  {"history": [float, ...], "horizon": int, "method": "ses"|"sma", "alpha"?: float, "window"?: int}
Output: {"ok": true, "output": {...forecast...}, "confidence": float}
"""

import json
import math
import sys


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return emit(False, errors=["stdin is not valid JSON"])

    history = payload.get("history") or []
    if not isinstance(history, list) or not history:
        return emit(False, errors=["history must be a non-empty array"])
    cleaned = [float(v) for v in history]

    horizon = int(payload.get("horizon", 7))
    horizon = max(1, min(365, horizon))
    method = str(payload.get("method", "ses")).lower()

    if method == "ses":
        alpha = float(payload.get("alpha", 0.4))
        alpha = max(0.05, min(0.95, alpha))
        level = cleaned[0]
        residuals = []
        for actual in cleaned[1:]:
            residuals.append(actual - level)
            level = alpha * actual + (1 - alpha) * level
        point = level
    elif method == "sma":
        window = max(1, int(payload.get("window", 7)))
        tail = cleaned[-window:]
        point = sum(tail) / len(tail)
        residuals = [v - point for v in cleaned]
    else:
        return emit(False, errors=[f"method must be 'ses' or 'sma', got {method!r}"])

    variance = sum(r * r for r in residuals) / len(residuals) if residuals else 0.0
    error_std = round(math.sqrt(variance), 4)
    forecast = [{"step": i + 1, "value": round(point, 4)} for i in range(horizon)]
    confidence = round(max(0.0, min(1.0, 1.0 - error_std / (abs(point) + 1e-6))), 4)

    return emit(True, output={
        "method": method,
        "history_points": len(cleaned),
        "horizon": horizon,
        "point_estimate": round(point, 4),
        "forecast": forecast,
        "error_stddev": error_std,
    }, confidence=confidence)


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
