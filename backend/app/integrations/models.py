"""Persistence for the three Atlassian loops.

Every row is unique-keyed so replays are idempotent. The outbox is the outage
buffer: edge nodes only ever write local health events here; the central worker
drains them to Atlassian under a shared rate limit.
"""

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Integer, String, Text
from sqlalchemy.sql import func

from app.db.database import Base


class ChangeApproval(Base):
    """Jira Change Request webhook approvals (release gate)."""

    __tablename__ = "int_change_approvals"

    id = Column(Integer, primary_key=True, index=True)
    issue_key = Column(String(64), unique=True, nullable=False, index=True)
    webhook_event_id = Column(String(128), nullable=False)
    status = Column(String(32), nullable=False, default="APPROVED")
    summary_hash = Column(String(64), nullable=False)
    received_at = Column(DateTime(timezone=True), server_default=func.now())


class ReleaseAudit(Base):
    """Immutable release evidence published to Confluence."""

    __tablename__ = "int_release_audits"

    id = Column(Integer, primary_key=True, index=True)
    version = Column(String(64), unique=True, nullable=False, index=True)
    manifest_hash = Column(String(64), nullable=False)
    checksums_hash = Column(String(64), nullable=False)
    page_id = Column(String(64), nullable=True)
    page_hash = Column(String(64), nullable=False)
    jira_issues = Column(Text, nullable=False, default="[]")
    published_at = Column(DateTime(timezone=True), server_default=func.now())


class EdgeIncident(Base):
    """JSM incident per store/incident key (store ID + architecture)."""

    __tablename__ = "int_edge_incidents"

    id = Column(Integer, primary_key=True, index=True)
    incident_key = Column(String(160), unique=True, nullable=False, index=True)
    tenant_id = Column(String(64), nullable=False)
    store_id = Column(String(64), nullable=False)
    architecture = Column(String(16), nullable=False)  # amd64 | arm64
    version = Column(String(64), nullable=True)
    jsm_issue_key = Column(String(64), nullable=True)
    status = Column(String(32), nullable=False, default="OPEN")
    runbook_page_id = Column(String(64), nullable=True)
    root_cause_hash = Column(String(64), nullable=True)
    opened_at = Column(DateTime(timezone=True), server_default=func.now())
    resolved_at = Column(DateTime(timezone=True), nullable=True)


class AtlassianOutbox(Base):
    """Durable, deduplicated, rate-limited delivery queue."""

    __tablename__ = "int_atlassian_outbox"

    id = Column(Integer, primary_key=True, index=True)
    idempotency_key = Column(String(200), unique=True, nullable=False, index=True)
    operation = Column(String(64), nullable=False)  # jsm.create_incident | jsm.resolve | confluence.publish | jira.comment
    payload = Column(Text, nullable=False)
    status = Column(String(32), nullable=False, default="PENDING", index=True)
    attempts = Column(Integer, nullable=False, default=0)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    delivered_at = Column(DateTime(timezone=True), nullable=True)
