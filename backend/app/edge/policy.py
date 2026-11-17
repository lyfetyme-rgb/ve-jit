"""Deterministic policy engine for edge events.

Policies are plain Python over the normalized manifest — no prompt can
override a HOLD/DENY. Built-in evaluators:

  weight_mismatch      measured weight deviates from expected by more than `tolerance_pct`
  quantity_threshold   abs(quantity) above `threshold` (inventory movement guard)
  unknown_device       source_device_id not in the `known_devices` param (or empty param -> always triggers)
  stale_heartbeat      no heartbeat seen within `max_age_seconds`
  unrecognized_event   event_type == edge.unrecognized (always matches)

Each PolicyRule row carries params JSON + requires_approval + action_class.
"""

import json
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.config import settings
from app.edge.models import AdapterHeartbeat, ContinuityScore, PolicyRule


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


# ------------------------------------------------------------------- hard safety gates
def applicable_rules(db: Session, action_class: str) -> list[PolicyRule]:
    return (
        db.query(PolicyRule)
        .filter(PolicyRule.active.is_(True))
        .filter(PolicyRule.action_class == action_class)
        .order_by(PolicyRule.id.asc())
        .all()
    )


# Step 1 of the two-step guardrail: boolean safety invariants. Step 2 (threshold
# rules weighted against the numeric continuity value) only runs when every
# invariant passes. Continuity value NEVER authorizes anything.

def hard_safety_gates(db: Session) -> dict:
    from app.edge import continuity

    state = continuity.current_state(db)
    safe_mode_clear = state != continuity.STATE_SAFE_MODE
    bundle_valid, bundle_detail = _policy_bundle_valid(db)
    credentials_valid, credential_detail = _credentials_valid()
    invariants = {
        "safe_mode_clear": {"passed": safe_mode_clear, "detail": f"state is {state}"},
        "policy_bundle_valid": {"passed": bundle_valid, "detail": bundle_detail},
        "credentials_valid": {"passed": credentials_valid, "detail": credential_detail},
    }
    permitted = all(v["passed"] for v in invariants.values())
    return {"permitted": permitted, "invariants": invariants, "state": state}


def latest_continuity_value(db: Session) -> Optional[float]:
    row = db.query(ContinuityScore).order_by(ContinuityScore.id.desc()).first()
    return row.value if row is not None else None


def _policy_bundle_valid(db: Session) -> tuple[bool, str]:
    """Hard safety gate invariant #2.

    Preferred path: a cryptographically verified, unexpired, monotonic signed
    policy bundle (Ed25519, overlapping trust ring).
    Fallback path (no signed bundle installed yet): at least one active local
    rule exists and the newest is fresher than the configured max age.
    """
    from app.edge import policy_bundle

    if policy_bundle.current_bundle_row(db) is not None:
        return policy_bundle.verify_current_bundle(db, max_age_seconds=float(settings.POLICY_BUNDLE_MAX_AGE_SECONDS))

    active = db.query(PolicyRule).filter(PolicyRule.active.is_(True)).all()
    if not active:
        return False, "no active policy rules; refusing to evaluate (fail closed)"
    max_age = float(settings.POLICY_BUNDLE_MAX_AGE_SECONDS)
    if max_age <= 0:
        return True, f"freshness check disabled ({len(active)} active rules)"
    newest = max((_aware(r.created_at) for r in active if r.created_at), default=None)
    if newest is None:
        return False, "policy bundle has no creation timestamp"
    age = (datetime.now(timezone.utc) - newest).total_seconds()
    if age > max_age:
        return False, f"policy bundle is stale ({age:.0f}s old; max {max_age:.0f}s)"
    return True, f"{len(active)} active rules, newest {age:.0f}s old"


def _credentials_valid() -> tuple[bool, str]:
    url = (settings.CLOUD_SYNC_URL or "").strip()
    if not url:
        # The edge holds no cloud credentials; cloud actions are unavailable, not unauthorized.
        # Local operation requires no cloud credential.
        return True, "edge holds no cloud credentials; cloud paths unavailable (local operation unaffected)"
    import os

    if os.environ.get("CLOUD_SYNC_TOKEN"):
        return True, "cloud credential present in environment"
    return False, "CLOUD_SYNC_URL is set but CLOUD_SYNC_TOKEN is missing; refusing cloud-dependent actions"


def require_safety_gates(db: Session) -> None:
    gates = hard_safety_gates(db)
    if not gates["permitted"]:
        failures = {name: inv["detail"] for name, inv in gates["invariants"].items() if not inv["passed"]}
        raise PolicyGateClosed(failures)


class PolicyGateClosed(Exception):
    def __init__(self, failures: dict):
        super().__init__(json.dumps(failures))
        self.failures = failures
        self.status_code = 409
        self.detail = "Hard safety gate closed: " + "; ".join(f"{k}={v}" for k, v in failures.items())


def _aware(value):
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


# ------------------------------------------------------------------- evaluation
def evaluate_event(db: Session, manifest: dict[str, Any], action_class: str = "general") -> dict:
    """Two-step guardrail evaluation.

    Step 1: hard safety invariants (state, policy bundle, credentials). If any fails,
            the decision is DENY and threshold rules are never consulted.
    Step 2: deterministic threshold rules. Any rule with requires_approval -> HOLD.
    """
    gates = hard_safety_gates(db)
    if not gates["permitted"]:
        return {
            "decision": "DENY",
            "requires_approval": False,
            "hits": [],
            "evaluated_rules": 0,
            "invariants": gates["invariants"],
            "continuity_value": latest_continuity_value(db),
        }

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
        "invariants": gates["invariants"],
        "continuity_value": latest_continuity_value(db),
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
    source_device_id = manifest.get("source_device_id", "")
    if known:
        return source_device_id not in known, f"device {source_device_id!r} not in known_devices allowlist"
    row = db.query(AdapterHeartbeat).filter(AdapterHeartbeat.device_id == source_device_id).first()
    if row is None:
        return True, f"device {source_device_id!r} has no registered heartbeat"
    return False, "device recognized"


def _stale_heartbeat(manifest, params, db):
    max_age = float(params.get("max_age_seconds", 300))
    source_device_id = manifest.get("source_device_id", "")
    row = db.query(AdapterHeartbeat).filter(AdapterHeartbeat.device_id == source_device_id).first()
    if row is None:
        return True, f"device {source_device_id!r} has never checked in"
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
