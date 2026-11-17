"""Continuity engine.

Two orthogonal concepts, deliberately kept separate:

1. The continuity **state machine** determines which operations are permitted.
   SAFE_MODE is an independent safety state, reachable from every other
   state. RECOVERING is an explicit transition out of OFFLINE/SAFE_MODE,
   never an automatic jump to ONLINE.

2. The continuity **value** quantifies the quality of the environment
   (0-100). It never authorizes an action. A high score cannot override a
   hard safety rule, an invalid policy or an invalid credential.
"""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.edge.models import (
    AdapterHeartbeat,
    ContinuityRecord,
    ContinuityScore,
    EDGE_STATES,
    EdgeOutbox,
    PolicyRule,
    RecoverySession,
)

STATE_ONLINE = "ONLINE"
STATE_DEGRADED = "DEGRADED"
STATE_OFFLINE = "OFFLINE"
STATE_SAFE_MODE = "SAFE_MODE"
STATE_RECOVERING = "RECOVERING"

# Hard transition table. SAFE_MODE is reachable from every state. ONLINE is
# only reachable via RECOVERING — never a direct jump — and only when the
# latest recovery session has COMPLETED the four ordered steps.
ALLOWED_TRANSITIONS = {
    STATE_ONLINE: {STATE_DEGRADED, STATE_OFFLINE, STATE_SAFE_MODE},
    STATE_DEGRADED: {STATE_OFFLINE, STATE_SAFE_MODE, STATE_RECOVERING},
    STATE_OFFLINE: {STATE_RECOVERING, STATE_SAFE_MODE, STATE_DEGRADED},
    STATE_SAFE_MODE: {STATE_RECOVERING},
    STATE_RECOVERING: {STATE_ONLINE, STATE_SAFE_MODE},
}

# Actions blocked per state. SAFE_MODE blocks everything except explicit safety-logged observation.
BLOCKED_IN_SAFE_MODE = {
    "inventory_move", "receiving_put_away", "purchase_order", "transfer_ship", "transfer_receive",
    "waste_record", "price_change", "promotion_change", "skill_execute",
}

COMPONENT_WEIGHTS = {
    "database_integrity": 20.0,
    "queue_durability": 15.0,
    "policy_freshness": 15.0,
    "catalog_freshness": 10.0,
    "inventory_freshness": 10.0,
    "device_health": 15.0,
    "credential_validity": 10.0,
    "sync_status": 5.0,
}


class ContinuityError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def current_state(db: Session) -> str:
    record = db.query(ContinuityRecord).order_by(ContinuityRecord.id.desc()).first()
    return record.to_state if record else STATE_ONLINE


def transition(db: Session, *, to_state: str, actor: str, reason: str) -> dict:
    if to_state not in EDGE_STATES:
        raise ContinuityError(422, f"Unknown state {to_state!r}; expected one of {EDGE_STATES}")
    if not actor or not actor.strip():
        raise ContinuityError(422, "A non-empty actor is required for state transitions")
    if not reason or not reason.strip():
        raise ContinuityError(422, "A reason is required for state transitions")

    from_state = current_state(db)
    if to_state == from_state:
        return {"from_state": from_state, "to_state": to_state, "changed": False}
    allowed = ALLOWED_TRANSITIONS.get(from_state, set())
    if to_state not in allowed:
        raise ContinuityError(
            409,
            f"Illegal transition {from_state} -> {to_state}. "
            f"Allowed from {from_state}: {sorted(allowed)}. "
            + ("Recovery is an explicit transition: use RECOVERING and the recovery flow, never a jump to ONLINE."
               if to_state == STATE_ONLINE and from_state != STATE_ONLINE else ""),
        )
    if to_state == STATE_ONLINE and from_state != STATE_ONLINE:
        # Hard gate: ONLINE requires the *latest* recovery session to be COMPLETED
        # (connectivity_check -> hash_verification -> watermark_replay -> cloud_ack).
        # A stale or failed recovery unlocks nothing.
        latest = db.query(RecoverySession).order_by(RecoverySession.id.desc()).first()
        if latest is None or latest.outcome != "COMPLETED":
            raise ContinuityError(
                409,
                "ONLINE is gated: the latest recovery session must have COMPLETED "
                "(connectivity_check -> hash_verification -> watermark_replay -> cloud_ack).",
            )
    db.add(ContinuityRecord(from_state=from_state, to_state=to_state, reason=reason.strip(), actor=actor.strip()))
    db.commit()
    return {"from_state": from_state, "to_state": to_state, "changed": True, "actor": actor.strip(), "reason": reason.strip()}


def require_action_allowed(db: Session, action_class: str) -> None:
    """Hard safety gate. SAFE_MODE blocks unsafe action classes regardless of continuity value."""
    state = current_state(db)
    if state == STATE_SAFE_MODE and action_class in BLOCKED_IN_SAFE_MODE:
        raise ContinuityError(
            409,
            f"SAFE_MODE is active; action class {action_class!r} is blocked. "
            "Explicit safety review and recovery are required before execution resumes.",
        )


def compute_continuity_value(db: Session, *, policy_max_age_seconds: float = 3600.0) -> dict:
    state = current_state(db)
    pending = db.query(EdgeOutbox).filter(EdgeOutbox.status == "PENDING").count()
    failed = db.query(EdgeOutbox).filter(EdgeOutbox.status == "FAILED").count()
    quarantined = db.query(EdgeOutbox).filter(EdgeOutbox.status == "QUARANTINED").count()
    heartbeats = db.query(AdapterHeartbeat).all()
    devices_total = len(heartbeats)
    devices_healthy = sum(1 for h in heartbeats if h.healthy)
    active_policies = db.query(PolicyRule).filter(PolicyRule.active.is_(True)).count()
    newest_policy = db.query(PolicyRule).order_by(PolicyRule.id.desc()).first()
    policy_age = (
        (datetime.now(timezone.utc) - _aware(newest_policy.created_at)).total_seconds()
        if newest_policy is not None else float("inf")
    )

    components = {
        # Local integrity: outbox is internally consistent (hashes present, no orphans beyond quarantine).
        "database_integrity": 100.0 if quarantined == 0 else max(0.0, 100.0 - 25.0 * quarantined),
        # Queue durability: penalize unsynced backlog and failures.
        "queue_durability": max(0.0, 100.0 - 2.0 * pending - 25.0 * failed),
        # Policy freshness: penalty for an empty/old policy set.
        "policy_freshness": 100.0 if (active_policies >= 3 and policy_age <= policy_max_age_seconds) else (60.0 if active_policies >= 1 else 0.0),
        # Freshness of catalog/inventory projections: unknown from inside the process; report not_measured honestly.
        "catalog_freshness": None,
        "inventory_freshness": None,
        # Device health from heartbeats; no heartbeats on record -> not measured, not assumed healthy.
        "device_health": (100.0 * devices_healthy / devices_total) if devices_total else None,
        # Credential validity: edge holds no cloud credentials; presence is configured at deploy time.
        "credential_validity": None,
        # Sync status from state machine.
        "sync_status": {STATE_ONLINE: 100.0, STATE_DEGRADED: 60.0, STATE_RECOVERING: 50.0, STATE_OFFLINE: 10.0, STATE_SAFE_MODE: 0.0}[state],
    }

    measured_weight = 0.0
    earned = 0.0
    breakdown = {}
    for component, weight in COMPONENT_WEIGHTS.items():
        value = components[component]
        if value is None:
            breakdown[component] = {"score": None, "weight": weight, "status": "not_measured"}
            continue
        measured_weight += weight
        earned += weight * (value / 100.0)
        breakdown[component] = {"score": round(value, 2), "weight": weight, "status": "measured"}

    value = round(100.0 * earned / measured_weight, 2) if measured_weight else 0.0

    score = ContinuityScore(
        state=state,
        value=value,
        components=_json(breakdown),
    )
    db.add(score)
    db.commit()

    return {
        "state": state,
        "value": value,
        "measured_weight": measured_weight,
        "components": breakdown,
        "hard_rules": "Continuity value never authorizes an action; SAFE_MODE and invalid policy/credential always block.",
    }


def _aware(value):
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _json(value) -> str:
    import json

    return json.dumps(value, sort_keys=True)
