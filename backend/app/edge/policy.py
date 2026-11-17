"""Deterministic policy engine for edge events.

Policies are plain Python over the normalized manifest — no prompt can
override a HOLD/DENY. Built-in evaluators:

  weight_mismatch      measured weight deviates from expected by more than `tolerance_pct`
  quantity_threshold   abs(quantity) above `threshold` (inventory movement guard)
  unknown_device       device_id not in the `known_devices` param (or empty param -> always triggers)
  stale_heartbeat      no heartbeat seen within `max_age_seconds`
  unrecognized_event   event_type == edge.unrecognized (always matches)

Each PolicyRule row carries params JSON + requires_approval + action_class.
"""

import json
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.edge.models import AdapterHeartbeat, PolicyRule


def evaluate_rule(rule: PolicyRule, manifest: dict[str, Any], db: Session) -> tuple[bool, str]:
    """Returns (matched, explanation). Deterministic."""
    params = _params(rule)
    evaluator = {
        "weight_mismatch": _weight_mismatch,
        "quantity_threshold": _quantity_threshold,
        "unknown_device": _unknown_device,
        "stale_heartbeat": _stale_heartbeat,
        "unrecognized_event": _unrecognized_event,
    }.get(rule.rule)
    if evaluator is None:
        return False, f"unknown policy evaluator {rule.rule!r}; rule ignored (fail closed is the schema's job)"
    return evaluator(manifest, params, db)


def applicable_rules(db: Session, action_class: str) -> list[PolicyRule]:
    return (
        db.query(PolicyRule)
        .filter(PolicyRule.active.is_(True))
        .filter(PolicyRule.action_class == action_class)
        .order_by(PolicyRule.id.asc())
        .all()
    )


def evaluate_event(db: Session, manifest: dict[str, Any], action_class: str = "general") -> dict:
    """Evaluate all active rules for the action class. Returns a decision."""
    hits = []
    for rule in applicable_rules(db, action_class):
        matched, explanation = evaluate_rule(rule, manifest, db)
        if matched:
            hits.append({
                "rule": rule.name,
                "rule_id": rule.id,
                "explanation": explanation,
                "requires_approval": rule.requires_approval,
                "action_class": rule.action_class,
                "policy_version": rule.policy_version,
            })
    requires_approval = any(hit["requires_approval"] for hit in hits)
    return {
        "decision": "HOLD" if requires_approval else ("FLAG" if hits else "ALLOW"),
        "requires_approval": requires_approval,
        "hits": hits,
        "evaluated_rules": len(applicable_rules(db, action_class)),
    }


def _params(rule: PolicyRule) -> dict:
    try:
        parsed = json.loads(rule.params or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except ValueError:
        return {}


def _weight_mismatch(manifest, params, db):
    expected = params.get("expected_weight_grams")
    measured = (manifest.get("payload") or {}).get("weight_grams")
    tolerance_pct = float(params.get("tolerance_pct", 2.0))
    if expected is None or measured is None:
        return False, "weight not present in payload"
    expected, measured = float(expected), float(measured)
    if expected == 0:
        return True, "expected weight is zero; cannot verify"
    deviation = abs(measured - expected) / expected * 100.0
    return deviation > tolerance_pct, f"weight deviation {deviation:.2f}% (tolerance {tolerance_pct}%)"


def _quantity_threshold(manifest, params, db):
    threshold = float(params.get("threshold", 50))
    payload = manifest.get("payload") or {}
    candidates = [
        payload.get("quantity"),
        (payload.get("quantity_delta")),
        (payload.get("lines") and sum(float(line.get("quantity", 0) or 0) for line in payload.get("lines") or [])),
    ]
    for candidate in candidates:
        if candidate is None:
            continue
        value = abs(float(candidate))
        if value > threshold:
            return True, f"quantity {value} exceeds threshold {threshold}"
    return False, "quantity within threshold"


def _unknown_device(manifest, params, db):
    known = params.get("known_devices") or []
    device_id = manifest.get("device_id", "")
    if known:
        return device_id not in known, f"device {device_id!r} not in known_devices allowlist"
    row = db.query(AdapterHeartbeat).filter(AdapterHeartbeat.device_id == device_id).first()
    if row is None:
        return True, f"device {device_id!r} has no registered heartbeat"
    return False, "device recognized"


def _stale_heartbeat(manifest, params, db):
    max_age = float(params.get("max_age_seconds", 300))
    device_id = manifest.get("device_id", "")
    row = db.query(AdapterHeartbeat).filter(AdapterHeartbeat.device_id == device_id).first()
    if row is None:
        return True, f"device {device_id!r} has never checked in"
    last_seen = row.last_seen
    if last_seen and last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - last_seen).total_seconds() if last_seen else float("inf")
    return age > max_age, f"heartbeat age {age:.0f}s (max {max_age}s)"


def _unrecognized_event(manifest, params, db):
    return manifest.get("event_type") == "edge.unrecognized", "event could not be mapped to a known vendor payload"


def seed_default_policies(db: Session) -> int:
    defaults = [
        {"name": "weight-mismatch-2pct", "rule": "weight_mismatch", "params": {"tolerance_pct": 2.0}, "requires_approval": True, "action_class": "receiving"},
        {"name": "quantity-threshold-50", "rule": "quantity_threshold", "params": {"threshold": 50}, "requires_approval": True, "action_class": "inventory"},
        {"name": "unknown-device-hold", "rule": "unknown_device", "params": {}, "requires_approval": True, "action_class": "general"},
        {"name": "stale-heartbeat-hold", "rule": "stale_heartbeat", "params": {"max_age_seconds": 300}, "requires_approval": True, "action_class": "general"},
        {"name": "unrecognized-hold", "rule": "unrecognized_event", "params": {}, "requires_approval": True, "action_class": "general"},
    ]
    created = 0
    for spec in defaults:
        if db.query(PolicyRule).filter(PolicyRule.name == spec["name"]).first() is not None:
            continue
        db.add(PolicyRule(
            name=spec["name"],
            rule=spec["rule"],
            params=json.dumps(spec["params"]),
            requires_approval=spec["requires_approval"],
            action_class=spec["action_class"],
        ))
        created += 1
    if created:
        db.commit()
    return created
