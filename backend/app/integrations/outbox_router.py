"""Outbox drain + outage resilience.

Central worker flushes queued Atlassian operations under the shared rate limit,
with dedup by idempotency key, bounded retries, priority lanes and a dead-letter
queue for permanent 4xx mapping drift. Edge nodes only ever see this through
their local ve-jit backend — never Atlassian APIs directly.
"""

import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import settings
from app.db.database import get_db
from app.integrations.client import AtlassianUnavailable, PermanentAtlassianError, build_client
from app.integrations.models import AtlassianOutbox

router = APIRouter(prefix="/api/v1/integrations", tags=["integrations"])

# Drain priority: P1 critical incidents first, then P3 info/telemetry, then P4
# Confluence housekeeping. Within a lane, oldest first.
PRIORITY_ORDER = (1, 3, 4)


class DrainIn(BaseModel):
    max_batch: int = Field(25, ge=1, le=500)


class DlqRetryIn(BaseModel):
    actor: str = Field(..., min_length=1)
    note: str = ""


@router.get("/outbox/status")
def outbox_status(db: Session = Depends(get_db)):
    def count(status: str) -> int:
        return db.query(AtlassianOutbox).filter(AtlassianOutbox.status == status).count()

    return {
        "pending": count("PENDING"),
        "failed": count("FAILED"),
        "failed_dlq": count("FAILED_DLQ"),
        "delivered": count("DELIVERED"),
        "atlassian_enabled": bool(settings.ATLASSIAN_ENABLED),
        "rate_limit_per_min": settings.ATLASSIAN_RATE_LIMIT_PER_MIN,
        "storm_window_seconds": settings.ATLASSIAN_STORM_WINDOW_SECONDS,
        "priority_lanes": list(PRIORITY_ORDER),
    }


@router.post("/outbox/drain")
def drain(body: DrainIn, db: Session = Depends(get_db), client: Any = None):
    target = client or build_client()
    rows = (
        db.query(AtlassianOutbox)
        .filter(AtlassianOutbox.status.in_(["PENDING", "FAILED"]))
        .order_by(AtlassianOutbox.priority.asc(), AtlassianOutbox.id.asc())
        .limit(body.max_batch)
        .all()
    )
    delivered = failed = dlq = 0
    processed: list[str] = []
    for row in rows:
        payload = json.loads(row.payload)
        try:
            if row.operation == "jira.comment":
                target.jira_comment(payload["issue"], payload["body"])
            elif row.operation == "jira.transition":
                target.jira_transition(payload["issue"], payload.get("transition", "31"))
            elif row.operation == "jsm.create_incident":
                result = target.jsm_create_incident(payload)
                if result.get("issue"):
                    _link_dry_run_issue(db, row, str(result["issue"]))
            elif row.operation == "jsm.resolve":
                target.jsm_transition(payload["issue"], payload.get("status", "Resolved"), payload["body"])
            elif row.operation == "confluence.publish":
                target.confluence_publish(payload["title"], payload["body"])
            else:
                raise AtlassianUnavailable(f"unknown operation {row.operation}")
            row.status = "DELIVERED"
            row.delivered_at = datetime.now(timezone.utc)
            row.attempts += 1
            delivered += 1
        except PermanentAtlassianError as exc:
            # Mapping drift (deleted project, renamed custom field, ...): do NOT
            # retry forever, and do NOT drop the payload. Quarantine it with the
            # full payload and surface it in the dashboard banner.
            row.status = "FAILED_DLQ"
            row.last_error = f"DLQ non-retryable {exc.status_code}: {exc.detail}"
            row.attempts += 1
            dlq += 1
        except (AtlassianUnavailable, KeyError) as exc:
            row.status = "FAILED"
            row.last_error = str(exc)[:500]
            row.attempts += 1
            failed += 1
        processed.append(f"P{row.priority}:{row.operation}:{row.id}")
    db.commit()
    return {
        "delivered": delivered,
        "failed": failed,
        "failed_dlq": dlq,
        "attempted": len(rows),
        "processed_order": processed,
        "dry_run": not bool(settings.ATLASSIAN_ENABLED),
    }


@router.get("/outbox/dlq")
def list_dlq(limit: int = 100, db: Session = Depends(get_db)):
    rows = (
        db.query(AtlassianOutbox)
        .filter(AtlassianOutbox.status == "FAILED_DLQ")
        .order_by(AtlassianOutbox.priority.asc(), AtlassianOutbox.id.asc())
        .limit(min(limit, 500))
        .all()
    )
    return {
        "items": [
            {
                "id": r.id,
                "idempotency_key": r.idempotency_key,
                "operation": r.operation,
                "priority": r.priority,
                "error": r.last_error,
                "attempts": r.attempts,
                "payload": json.loads(r.payload),
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
    }


@router.post("/outbox/dlq/{row_id}/retry")
def retry_dlq(row_id: int, body: DlqRetryIn, db: Session = Depends(get_db)):
    """Requeue a quarantined item once the mapping drift has been fixed."""
    row = db.query(AtlassianOutbox).filter(AtlassianOutbox.id == row_id).first()
    if row is None or row.status != "FAILED_DLQ":
        raise HTTPException(status_code=404, detail=f"DLQ item {row_id} not found")
    row.status = "PENDING"
    row.last_error = f"requeued by {body.actor}: {body.note[:200]}" if body.note else f"requeued by {body.actor}"
    db.commit()
    return {"ok": True, "id": row.id, "status": "PENDING", "requeued_by": body.actor}


def _link_dry_run_issue(db: Session, row: AtlassianOutbox, jsm_issue: str) -> None:
    """In dry-run, the fake client returns an issue key; attach it to the incident."""
    from app.integrations.models import EdgeIncident

    payload = json.loads(row.payload)
    description = payload.get("description", "")
    marker = description.split("store=")[1].split(";")[0] if "store=" in description else ""
    if marker:
        incident = (
            db.query(EdgeIncident)
            .filter(EdgeIncident.store_id == marker, EdgeIncident.jsm_issue_key.like("JSM-%"))
            .order_by(EdgeIncident.id.desc())
            .first()
        )
        if incident and not incident.jsm_issue_key.startswith("JSM-000"):
            return
        if incident:
            incident.jsm_issue_key = jsm_issue
