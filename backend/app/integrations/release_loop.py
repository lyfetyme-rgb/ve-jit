"""Loop 1 — Release & Audit Loop.

Jira Change Request Approved webhook -> unlock deployment gate.
Successful release verification -> immutable Confluence audit page
(version + manifest.json + SHA256SUMS.txt + linked Jira issues).
"""

import hashlib
import hmac
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import settings
from app.db.database import get_db
from app.integrations.client import DryRunAtlassianClient, build_client, content_hash, enqueue
from app.integrations.models import ChangeApproval, ReleaseAudit

router = APIRouter(prefix="/api/v1/integrations", tags=["integrations"])


class ChangeWebhook(BaseModel):
    event_id: str = Field(..., min_length=1)
    issue_key: str = Field(..., min_length=1)
    status: str = Field(..., min_length=1)
    summary: str = ""
    version: str = ""


class ReleaseAuditIn(BaseModel):
    version: str = Field(..., min_length=1)
    manifest: dict[str, Any]
    checksums: str = Field(..., min_length=1)
    jira_issues: list[str] = Field(default_factory=list)
    verification: str = "green"  # release workflow status


class GateOut(BaseModel):
    issue_key: str
    unlocked: bool
    version: str
    status: str


def _webhook_secret() -> bytes:
    path = Path(settings.ATLASSIAN_WEBHOOK_SECRET_FILE)
    if not path.exists() or not path.read_text(encoding="utf-8").strip():
        raise HTTPException(status_code=503, detail="Jira webhook secret is not configured (central backend only)")
    return path.read_text(encoding="utf-8").strip().encode("utf-8")


@router.post("/jira/change-approved")
def change_approved(
    body: ChangeWebhook,
    db: Session = Depends(get_db),
    x_hub_signature_256: str = Header(default=""),
):
    expected = "sha256=" + hmac.new(_webhook_secret(), body.model_dump_json().encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, x_hub_signature_256):
        raise HTTPException(status_code=401, detail="Invalid Jira webhook signature")
    if body.status.upper() not in ("APPROVED", "DONE"):
        raise HTTPException(202, detail=f"Ignored status {body.status}; no gate change")

    existing = db.query(ChangeApproval).filter(ChangeApproval.issue_key == body.issue_key).first()
    if existing is not None:
        return {"ok": True, "changed": False, "issue_key": body.issue_key, "status": existing.status}

    db.add(ChangeApproval(
        issue_key=body.issue_key,
        webhook_event_id=body.event_id,
        status="APPROVED",
        summary_hash=content_hash(body.summary or body.issue_key),
    ))
    # Enqueue the Jira audit comment through the rate-limited outbox.
    enqueue(db, "jira.comment", f"change:{body.issue_key}:{body.event_id}", {
        "issue": body.issue_key,
        "body": f"VE-JIT: change {body.issue_key} approved; production gate unlocked for {body.version or 'next release'}.",
    })
    db.commit()
    return {"ok": True, "changed": True, "issue_key": body.issue_key, "status": "APPROVED"}


@router.get("/jira/release-gate/{issue_key}", response_model=GateOut)
def release_gate(issue_key: str, db: Session = Depends(get_db)):
    row = db.query(ChangeApproval).filter(ChangeApproval.issue_key == issue_key).first()
    if row is None or row.status != "APPROVED":
        raise HTTPException(status_code=403, detail=f"No approved change request {issue_key}; production gate locked")
    return GateOut(issue_key=row.issue_key, unlocked=True, version="approved", status=row.status)


def _release_page_body(version: str, manifest_hash: str, checksums_hash: str, issues: list[str], verified: str) -> str:
    lines = [
        f"<h1>VE-JIT Release Audit — {version}</h1>",
        f"<p>Status: <strong>{verified}</strong></p>",
        f"<p>Manifest (manifest.json) SHA256: <code>{manifest_hash}</code></p>",
        f"<p>SHA256SUMS.txt SHA256: <code>{checksums_hash}</code></p>",
        "<h2>Linked Jira issues</h2>",
        "<ul>" + "".join(f"<li>{i}</li>" for i in issues) + "</ul>",
        "<p>Immutability note: content hash + append-only local audit record; page edits invalidate the hash.</p>",
    ]
    return "\n".join(lines)


@router.post("/release/audit")
def publish_release_audit(body: ReleaseAuditIn, db: Session = Depends(get_db), client: Any = None):
    if body.verification.lower() != "green":
        raise HTTPException(status_code=409, detail=f"Release verification is {body.verification}; audit page requires a green run")
    existing = db.query(ReleaseAudit).filter(ReleaseAudit.version == body.version).first()
    if existing is not None:
        return {"ok": True, "changed": False, "version": body.version, "page_id": existing.page_id}

    manifest_hash = content_hash(json.dumps(body.manifest, sort_keys=True, default=str))
    checksums_hash = content_hash(body.checksums)
    page_body = _release_page_body(body.version, manifest_hash, checksums_hash, body.jira_issues, body.verification)
    target = client or build_client()
    result = target.confluence_publish(f"VE-JIT Release Audit {body.version}", page_body)
    page_id = str(result.get("page_id", ""))

    db.add(ReleaseAudit(
        version=body.version,
        manifest_hash=manifest_hash,
        checksums_hash=checksums_hash,
        page_id=page_id,
        page_hash=content_hash(page_body),
        jira_issues=json.dumps(body.jira_issues),
    ))
    enqueue(db, "jira.comment", f"release:{body.version}:audit", {
        "issue": body.jira_issues[0] if body.jira_issues else settings.JIRA_PROJECT_KEY,
        "body": f"VE-JIT {body.version}: release audit Confluence page {page_id} (manifest {manifest_hash[:16]}).",
    })
    db.commit()
    return {
        "ok": True,
        "changed": True,
        "version": body.version,
        "page_id": page_id,
        "manifest_hash": manifest_hash,
        "checksums_hash": checksums_hash,
        "dry_run": isinstance(target, DryRunAtlassianClient),
    }


@router.get("/release/audit/{version}")
def get_release_audit(version: str, db: Session = Depends(get_db)):
    row = db.query(ReleaseAudit).filter(ReleaseAudit.version == version).first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"No audit record for {version}")
    return {
        "version": row.version,
        "manifest_hash": row.manifest_hash,
        "checksums_hash": row.checksums_hash,
        "page_id": row.page_id,
        "page_hash": row.page_hash,
        "jira_issues": json.loads(row.jira_issues),
        "published_at": row.published_at.isoformat() if row.published_at else None,
    }
