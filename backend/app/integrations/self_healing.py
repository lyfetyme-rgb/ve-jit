"""Loop 2 — Store Self-Healing Loop.

Edge nodes POST health events to ve-jit central only (never to Atlassian).
Central deduplicates storm alerts and opens a JSM incident tagged with the
store ID and architecture (amd64/arm64) plus a Confluence runbook link.
When rollback + health checks pass, the incident gets root-cause logs and is
transitioned to Resolved.
"""

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import settings
from app.db.database import get_db
from app.integrations.client import build_client, content_hash, enqueue
from app.integrations.models import EdgeIncident

router = APIRouter(prefix="/api/v1/integrations", tags=["integrations"])

ARCHITECTURES = ("amd64", "arm64")


class EdgeHealthIn(BaseModel):
    tenant_id: str = Field(..., min_length=1)
    store_id: str = Field(..., min_length=1)
    architecture: str = Field(..., pattern="^(amd64|arm64)$")
    healthy: bool
    version: str = ""
    runbook_page_id: str = ""
    root_cause_log_hash: str = ""
    reason: str = ""


@router.post("/edge/health")
def edge_health(body: EdgeHealthIn, db: Session = Depends(get_db)):
    incident_key = f"{body.tenant_id}:{body.store_id}:{body.architecture}"
    row = db.query(EdgeIncident).filter(EdgeIncident.incident_key == incident_key).first()

    if body.healthy:
        if row is None:
            return {"ok": True, "changed": False, "status": "HEALTHY_NO_INCIDENT", "incident_key": incident_key}
        if row.status == "RESOLVED":
            return {"ok": True, "changed": False, "status": "RESOLVED", "incident_key": incident_key}
        if not body.root_cause_log_hash:
            raise HTTPException(status_code=422, detail="Recovery requires root_cause_log_hash")
        row.status = "RESOLVED"
        row.resolved_at = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
        row.root_cause_hash = body.root_cause_log_hash
        enqueue(db, "jsm.resolve", f"resolve:{incident_key}:{body.root_cause_log_hash}", {
            "issue": row.jsm_issue_key,
            "status": "Resolved",
            "body": f"VE-JIT store {body.store_id} ({body.architecture}) recovered after rollback. "
                    f"Root-cause logs digest: {body.root_cause_log_hash}. Reason: {body.reason}",
        })
        db.commit()
        return {"ok": True, "changed": True, "status": "RESOLVED", "incident_key": incident_key, "jsm_issue": row.jsm_issue_key}

    if row is not None and row.status == "OPEN":
        # Storm dedupe: one incident per store+arch, never one per health check.
        return {"ok": True, "changed": False, "status": "ALREADY_OPEN", "incident_key": incident_key, "jsm_issue": row.jsm_issue_key}

    runbook = body.runbook_page_id or settings.CONFLUENCE_ROOT_PAGE_ID
    payload = {
        "service_desk_id": settings.JSM_SERVICE_DESK_ID,
        "summary": f"VE-JIT edge unhealthy: {body.store_id} ({body.architecture})",
        "description": f"tenant={body.tenant_id}; store={body.store_id}; arch={body.architecture}; "
                       f"version={body.version}; runbook={runbook}; reason={body.reason}",
    }
    enqueue(db, "jsm.create_incident", f"open:{incident_key}", payload)
    row = EdgeIncident(
        incident_key=incident_key,
        tenant_id=body.tenant_id,
        store_id=body.store_id,
        architecture=body.architecture,
        version=body.version,
        status="OPEN",
        runbook_page_id=runbook,
    )
    db.add(row)
    db.flush()
    # Assign a deterministic dry-run issue id; real delivery overwrites on outbox drain.
    row.jsm_issue_key = row.jsm_issue_key or f"JSM-{row.id:06d}"
    db.commit()
    return {"ok": True, "changed": True, "status": "OPEN", "incident_key": incident_key, "jsm_issue": row.jsm_issue_key, "runbook_page_id": runbook}


@router.get("/jsm/incidents")
def list_incidents(status: str = "", limit: int = 100, db: Session = Depends(get_db)):
    query = db.query(EdgeIncident)
    if status:
        query = query.filter(EdgeIncident.status == status.upper())
    rows = query.order_by(EdgeIncident.id.desc()).limit(min(limit, 500)).all()
    return {"incidents": [
        {
            "incident_key": r.incident_key,
            "store_id": r.store_id,
            "architecture": r.architecture,
            "status": r.status,
            "jsm_issue": r.jsm_issue_key,
            "runbook_page_id": r.runbook_page_id,
            "root_cause_hash": r.root_cause_hash,
            "opened_at": r.opened_at.isoformat() if r.opened_at else None,
            "resolved_at": r.resolved_at.isoformat() if r.resolved_at else None,
        }
        for r in rows
    ]}
