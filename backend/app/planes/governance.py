"""Recommendation lifecycle and the authorization boundary.

The rule the whole platform runs on:

    Intelligence produces recommendations.
    Operations executes authorized business actions.
    Commerce remains responsible for the customer-facing transaction.

A recommendation is not authority. Operations will only execute a
recommendation whose status is APPROVED, and it records exactly who approved
and who executed it. Approval is a human decision made out of band or via the
approval endpoint; there is no path from PROPOSED to EXECUTED in one call.
"""

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.planes.models import Recommendation


class RecommendationError(Exception):
    """Domain error for recommendation lifecycle violations."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def new_uid() -> str:
    return f"rec_{uuid.uuid4().hex[:12]}"


def create_recommendation(
    db: Session,
    *,
    kind: str,
    subject_type: str,
    subject_id: str,
    summary: str,
    rationale: Optional[str] = None,
    confidence: float = 0.5,
    recommended_action: Optional[str] = None,
    created_by: str = "intelligence-engine",
) -> Recommendation:
    rec = Recommendation(
        uid=new_uid(),
        plane="intelligence",
        kind=kind,
        subject_type=subject_type,
        subject_id=subject_id,
        summary=summary,
        rationale=rationale,
        confidence=max(0.0, min(1.0, float(confidence))),
        recommended_action=recommended_action,
        created_by=created_by,
        status="PROPOSED",
    )
    db.add(rec)
    db.commit()
    db.refresh(rec)
    return rec


def get_recommendation(db: Session, uid: str) -> Recommendation:
    rec = db.query(Recommendation).filter(Recommendation.uid == uid).first()
    if rec is None:
        raise RecommendationError(404, f"Recommendation {uid} not found")
    return rec


def approve_recommendation(db: Session, uid: str, *, approver: str, note: Optional[str] = None) -> Recommendation:
    _require_actor(approver, "approver")
    rec = get_recommendation(db, uid)
    if rec.status == "REJECTED":
        raise RecommendationError(409, "Recommendation was already rejected")
    if rec.status == "EXECUTED":
        raise RecommendationError(409, "Recommendation was already executed")
    if rec.status == "APPROVED":
        return rec
    rec.status = "APPROVED"
    rec.version = (rec.version or 1) + 1
    rec.approved_by = approver.strip()
    rec.approved_at = datetime.now(timezone.utc)
    if note:
        rec.note = note
    db.commit()
    db.refresh(rec)
    return rec


def reject_recommendation(db: Session, uid: str, *, actor: str, note: Optional[str] = None) -> Recommendation:
    _require_actor(actor, "actor")
    rec = get_recommendation(db, uid)
    if rec.status == "EXECUTED":
        raise RecommendationError(409, "Recommendation was already executed")
    if rec.status == "APPROVED":
        raise RecommendationError(409, "Recommendation already approved; reject before execution is not allowed")
    rec.status = "REJECTED"
    rec.version = (rec.version or 1) + 1
    rec.note = note
    db.commit()
    db.refresh(rec)
    return rec


def require_approvable_for_execution(db: Session, uid: str) -> Recommendation:
    rec = get_recommendation(db, uid)
    if rec.status != "APPROVED":
        raise RecommendationError(
            409,
            f"Recommendation {uid} is {rec.status}; only APPROVED recommendations can be executed by Operations",
        )
    return rec


def mark_executed(db: Session, rec: Recommendation, *, actor: str, execution_ref: Optional[str], note: Optional[str] = None) -> Recommendation:
    _require_actor(actor, "actor")
    rec.status = "EXECUTED"
    rec.version = (rec.version or 1) + 1
    rec.executed_by = actor.strip()
    rec.executed_at = datetime.now(timezone.utc)
    rec.execution_ref = execution_ref
    if note:
        rec.note = note
    db.commit()
    db.refresh(rec)
    return rec


def _require_actor(actor: Optional[str], field: str) -> None:
    if not actor or not actor.strip():
        raise RecommendationError(422, f"A non-empty {field} is required; there is no anonymous path")


def recommendation_to_dict(rec: Recommendation) -> dict:
    return {
        "uid": rec.uid,
        "plane": rec.plane,
        "kind": rec.kind,
        "subject_type": rec.subject_type,
        "subject_id": rec.subject_id,
        "summary": rec.summary,
        "rationale": rec.rationale,
        "confidence": rec.confidence,
        "recommended_action": _json_or_raw(rec.recommended_action),
        "status": rec.status,
        "created_by": rec.created_by,
        "approved_by": rec.approved_by,
        "approved_at": rec.approved_at.isoformat() if rec.approved_at else None,
        "executed_by": rec.executed_by,
        "executed_at": rec.executed_at.isoformat() if rec.executed_at else None,
        "execution_ref": rec.execution_ref,
        "note": rec.note,
        "created_at": rec.created_at.isoformat() if rec.created_at else None,
    }


def _json_or_raw(value: Optional[str]):
    if value is None:
        return None
    import json

    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return value
