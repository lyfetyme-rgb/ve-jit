"""Tests for the three Atlassian automation loops (injected dry-run client)."""

import hashlib
import hmac
import json
import os
import tempfile
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.db.database import Base, get_db
import app.planes.models  # noqa: F401
import app.edge.models  # noqa: F401
import app.cloud.models  # noqa: F401
import app.integrations.models  # noqa: F401
from app.main import app

_tmp = os.path.join(tempfile.gettempdir(), f"vejit_atlassian_test_{uuid.uuid4().hex}.db")
engine = create_engine(f"sqlite:///{_tmp}", connect_args={"check_same_thread": False})
S = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override():
    db = S()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def _isolate():
    app.dependency_overrides[get_db] = override
    secret = Path(tempfile.gettempdir()) / f"vejit_hook_{uuid.uuid4().hex}.txt"
    secret.write_text("test-webhook-secret", encoding="utf-8")
    old_secret, old_enabled = settings.ATLASSIAN_WEBHOOK_SECRET_FILE, settings.ATLASSIAN_ENABLED
    settings.ATLASSIAN_WEBHOOK_SECRET_FILE = str(secret)
    settings.ATLASSIAN_ENABLED = False  # dry-run client in tests
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_db, None)
        settings.ATLASSIAN_WEBHOOK_SECRET_FILE = old_secret
        settings.ATLASSIAN_ENABLED = old_enabled
        secret.unlink(missing_ok=True)


Base.metadata.create_all(bind=engine)
client = TestClient(app)


def signed(body: dict) -> str:
    raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    # FastAPI model_dump_json has compact separators by default.
    return "sha256=" + hmac.new(b"test-webhook-secret", raw, hashlib.sha256).hexdigest()


def hook_payload(issue="KAN-42", status="Approved", event="evt-1", version="v0.3.0"):
    return {"event_id": event, "issue_key": issue, "status": status, "summary": "deploy v0.3.0", "version": version}


def test_release_gate_requires_approved_webhook():
    assert client.get("/api/v1/integrations/jira/release-gate/KAN-99").status_code == 403
    body = hook_payload()
    resp = client.post("/api/v1/integrations/jira/change-approved", json=body, headers={"x-hub-signature-256": signed(body)})
    assert resp.status_code == 200, resp.text
    assert resp.json()["changed"] is True
    assert client.get("/api/v1/integrations/jira/release-gate/KAN-42").json()["unlocked"] is True
    # Idempotent replay
    again = client.post("/api/v1/integrations/jira/change-approved", json=body, headers={"x-hub-signature-256": signed(body)})
    assert again.json()["changed"] is False


def test_bad_webhook_signature_rejected():
    body = hook_payload(issue="KAN-77")
    resp = client.post("/api/v1/integrations/jira/change-approved", json=body, headers={"x-hub-signature-256": "sha256=bad"})
    assert resp.status_code == 401
    assert client.get("/api/v1/integrations/jira/release-gate/KAN-77").status_code == 403


def test_release_audit_publishes_hashed_page_and_requires_green():
    body = {
        "version": "v0.3.0",
        "manifest": {"schema": "vendarix.release.manifest.v1", "platforms": {"amd64": {}, "arm64": {}}},
        "checksums": "abc  file.deb\ndef  file.tar.gz\n",
        "jira_issues": ["KAN-42", "KAN-43"],
        "verification": "green",
    }
    bad = client.post("/api/v1/integrations/release/audit", json={**body, "verification": "red"})
    assert bad.status_code == 409
    ok = client.post("/api/v1/integrations/release/audit", json=body)
    assert ok.status_code == 200, ok.text
    result = ok.json()
    assert result["dry_run"] is True
    assert len(result["manifest_hash"]) == 64
    assert len(result["checksums_hash"]) == 64
    record = client.get("/api/v1/integrations/release/audit/v0.3.0").json()
    assert record["jira_issues"] == ["KAN-42", "KAN-43"]
    again = client.post("/api/v1/integrations/release/audit", json=body)
    assert again.json()["changed"] is False


def test_store_loop_dedupes_storms_and_resolves():
    base = {"tenant_id": "tenant_001", "store_id": "store_va_042", "architecture": "arm64", "version": "0.3.0"}
    first = client.post("/api/v1/integrations/edge/health", json={**base, "healthy": False, "reason": "warmup failed"})
    assert first.json()["status"] == "OPEN"
    for _ in range(10):
        storm = client.post("/api/v1/integrations/edge/health", json={**base, "healthy": False})
        assert storm.json()["status"] == "ALREADY_OPEN"
    incidents = client.get("/api/v1/integrations/jsm/incidents?status=OPEN").json()["incidents"]
    assert len([i for i in incidents if i["store_id"] == "store_va_042"]) == 1
    assert incidents[-1]["architecture"] == "arm64"

    missing_hash = client.post("/api/v1/integrations/edge/health", json={**base, "healthy": True})
    assert missing_hash.status_code == 422
    recovered = client.post("/api/v1/integrations/edge/health", json={
        **base, "healthy": True, "root_cause_log_hash": "a" * 64, "reason": "rolled back to 0.2.0"
    })
    assert recovered.json()["status"] == "RESOLVED"
    resolved = client.get("/api/v1/integrations/jsm/incidents?status=RESOLVED").json()["incidents"]
    assert any(i["store_id"] == "store_va_042" and i["root_cause_hash"] == "a" * 64 for i in resolved)


def test_outbox_drains_deduped_operations():
    status = client.get("/api/v1/integrations/outbox/status").json()
    assert status["pending"] >= 1
    result = client.post("/api/v1/integrations/outbox/drain", json={"max_batch": 50})
    assert result.status_code == 200
    assert result.json()["dry_run"] is True
    after = client.get("/api/v1/integrations/outbox/status").json()
    assert after["pending"] == 0
    assert "failed_dlq" in after
    assert after["priority_lanes"] == [1, 3, 4]


def _enqueue(db_session, operation: str, key: str, payload: dict, priority: int):
    from app.integrations.client import enqueue

    row = enqueue(db_session, operation, key, payload, priority=priority)
    db_session.commit()
    return row.id


def test_priority_lanes_drain_p1_before_p3_p4():
    from app.integrations.client import enqueue

    db = S()
    try:
        # P4 Confluence page update first in insertion order, then P3 info, then P1 critical.
        enqueue(db, "confluence.publish", "prio-p4", {"title": "t", "body": "b"}, priority=4)
        enqueue(db, "jira.comment", "prio-p3", {"issue": "X", "body": "info"}, priority=3)
        enqueue(db, "jsm.create_incident", "prio-p1", {"description": "cold-chain failure"}, priority=1)
        db.commit()
    finally:
        db.close()

    result = client.post("/api/v1/integrations/outbox/drain", json={"max_batch": 3}).json()
    order = result["processed_order"]
    assert len(order) == 3
    assert order[0].startswith("P1:"), order
    assert order[1].startswith("P3:"), order
    assert order[2].startswith("P4:"), order


def test_permanent_4xx_goes_to_dlq_with_payload_retained():
    from app.integrations.client import PermanentAtlassianError
    from app.integrations.models import AtlassianOutbox

    db = S()
    try:
        row = AtlassianOutbox(
            idempotency_key="dlq-test-1", operation="jira.comment",
            payload=json.dumps({"issue": "KAN-GONE", "body": "audit"}),
            priority=3, status="PENDING",
        )
        db.add(row)
        db.commit()
        dlq_id = row.id
    finally:
        db.close()

    class FakeClient:
        def jira_comment(self, issue, body):
            raise PermanentAtlassianError(400, "project deleted")

    # Inject a failing client through app state is not needed: call the drain
    # with an injected dependency-free wrapper via monkeypatch on build_client.
    import app.integrations.outbox_router as outbox_module

    original = outbox_module.build_client
    outbox_module.build_client = lambda: FakeClient()
    try:
        result = client.post("/api/v1/integrations/outbox/drain", json={"max_batch": 50}).json()
        assert result["failed_dlq"] >= 1
    finally:
        outbox_module.build_client = original

    db = S()
    try:
        row = db.query(AtlassianOutbox).filter(AtlassianOutbox.id == dlq_id).first()
        assert row.status == "FAILED_DLQ"
        assert "400" in row.last_error
        assert json.loads(row.payload)["issue"] == "KAN-GONE"  # audit data retained
    finally:
        db.close()

    listing = client.get("/api/v1/integrations/outbox/dlq").json()["items"]
    assert any(item["idempotency_key"] == "dlq-test-1" for item in listing)
    requeue = client.post(f"/api/v1/integrations/outbox/dlq/{dlq_id}/retry",
                          json={"actor": "admin-1", "note": "restored project"})
    assert requeue.status_code == 200
    assert requeue.json()["status"] == "PENDING"


def test_critical_reason_assigns_p1_priority():
    base = {"tenant_id": "tenant_001", "store_id": "store-p1", "architecture": "amd64", "version": "0.3.0"}
    client.post("/api/v1/integrations/edge/health", json={
        **base, "healthy": False, "reason": "QSR POS DOWN - register lane offline"
    })
    db = S()
    try:
        from app.integrations.models import AtlassianOutbox

        rows = db.query(AtlassianOutbox).filter(AtlassianOutbox.idempotency_key.like("open:tenant_001:store-p1:%")).all()
        assert rows and rows[-1].priority == 1
    finally:
        db.close()


def test_edge_modules_never_import_atlassian():
    edge_root = Path(__file__).resolve().parents[1] / "app" / "edge"
    # Edge may use httpx for the edge->cloud sync URL, but must never import
    # the central Atlassian integration package or hold Atlassian credentials.
    forbidden = ("integrations", "atlassian")
    for path in edge_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert f"import {token}" not in text and f"from {token}" not in text, f"{path} must not import {token}"


def test_token_bucket_rate_limit():
    from app.integrations.client import TokenBucket

    bucket = TokenBucket(rate_per_min=60, burst=1)
    assert bucket.acquire() is True
    assert bucket.acquire() is False
