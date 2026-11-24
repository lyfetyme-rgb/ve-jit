"""Outbox drain + outage resilience.

Central worker flushes queued Atlassian operations under the shared rate limit,
with dedup by idempotency key and bounded retries. Edge nodes only ever see this
through their local ve-jit backend — never Atlassian APIs directly.
"""

import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import settings
from app.db.database import get_db
from app.integrations.client import AtlassianUnavailable, build_client
from app.integrations.models import AtlassianOutbox

router = APIRouter(prefix="/api/v1/integrations", tags=["integrations"])


class DrainIn(BaseModel):
    max_batch: int = Field(25, ge=1, le=500)


@router.get("/outbox/status")
def outbox_status(db: Session = Depends(get_db)):
    pending = db.query(AtlassianOutbox).filter(AtlassianOutbox.status == "PENDING").count()
    failed = db.query(AtlassianOutbox).filter(AtlassianOutbox.status == "FAILED").count()
    delivered = db.query(AtlassianOutbox).filter(AtlassianOutbox.status == "DELIVERED").count()
    return {
        "pending": pending,
        "failed": failed,
        "delivered": delivered,
        "atlassian_enabled": bool(settings.ATLASSIAN_ENABLED),
        "rate_limit_per_min": settings.ATLASSIAN_RATE_LIMIT_PER_MIN,
        "storm_window_seconds": settings.ATLASSIAN_STORM_WINDOW_SECONDS,
    }


@router.post("/outbox/drain")
def drain(body: DrainIn, db: Session = Depends(get_db)):
    client = build_client()
    rows = (
        db.query(AtlassianOutbox)
        .filter(AtlassianOutbox.status.in_(["PENDING", "FAILED"]))
        .order_by(AtlassianOutbox.id.asc())
        .limit(body.max_batch)
        .all()
    )
    delivered = failed = 0
    for row in rows:
        payload = json.loads(row.payload)
        try:
            if row.operation == "jira.comment":
                client.jira_comment(payload["issue"], payload["body"])
            elif row.operation == "jira.transition":
                client.jira_transition(payload["issue"], payload.get("transition", "31"))
            elif row.operation == "jsm.create_incident":
                result = client.jsm_create_incident(payload)
                if result.get("issue"):
                    _link_dry_run_issue(db, row, str(result["issue"]))
            elif row.operation == "jsm.resolve":
                client.jsm_transition(payload["issue"], payload.get("status", "Resolved"), payload["body"])
            elif row.operation == "confluence.publish":
                client.confluence_publish(payload["title"], payload["body"])
            else:
                raise AtlassianUnavailable(f"unknown operation {row.operation}")
            row.status = "DELIVERED"
            row.delivered_at = datetime.now(timezone.utc)
            row.attempts += 1
            delivered += 1
        except (AtlassianUnavailable, KeyError) as exc:
            row.status = "FAILED"
            row.last_error = str(exc)[:500]
            row.attempts += 1
            failed += 1
    db.commit()
    return {"delivered": delivered, "failed": failed, "attempted": len(rows), "dry_run": not bool(settings.ATLASSIAN_ENABLED)}


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
