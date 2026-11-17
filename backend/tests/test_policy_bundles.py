"""Policy bundle signing, verification, trust ring and hard-gate integration."""

import base64
import json
import os
import tempfile
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("DATABASE_URL", "sqlite:///./vejit.db")

from app.cloud import policy_signer
from app.config import settings
from app.db.database import Base, get_db
import app.planes.models  # noqa: F401
import app.edge.models  # noqa: F401
import app.cloud.models  # noqa: F401
from app.edge.policy_verifier import PolicyVerificationError, load_public_key_b64, verify_edge_policy_bundle
from app.main import app

_tmp = os.path.join(tempfile.gettempdir(), f"vejit_policy_test_{uuid.uuid4().hex}.db")
engine = create_engine(f"sqlite:///{_tmp}", connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db
Base.metadata.create_all(bind=engine)


@pytest.fixture(autouse=True)
def _isolate_db():
    app.dependency_overrides[get_db] = override_get_db
    db = TestingSessionLocal()
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_db, None)
        db.close()


client = TestClient(app)

KEY = Ed25519PrivateKey.generate()
PUBLIC_B64 = base64.urlsafe_b64encode(KEY.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
KID = f"vendarix-pol-{uuid.uuid4().hex[:6]}"


def _seed_env():
    old_token, old_key = settings.CLOUD_SIGNER_TOKEN, settings.POLICY_SIGNING_KEY
    settings.CLOUD_SIGNER_TOKEN = "test-token"
    settings.POLICY_SIGNING_KEY = policy_signer.private_key_to_seed_b64(KEY)
    return old_token, old_key


def _restore_env(saved):
    settings.CLOUD_SIGNER_TOKEN, settings.POLICY_SIGNING_KEY = saved


def _register_key(role="staged"):
    resp = client.post("/api/v1/edge/policy/keys", json={
        "kid": KID, "public_key_b64": PUBLIC_B64, "role": role, "actor": "test-admin",
    })
    assert resp.status_code in (201, 409), resp.text


def _sign_and_install(version=1, ttl_seconds=86400):
    saved = _seed_env()
    try:
        sign = client.post("/api/v1/cloud/policy/bundles/sign", json={
            "payload": {"tenant_id": "tenant_retail_01", "store_id": "store_va_042",
                        "rules": {"max_auto_refund_cents": 15000}},
            "key_id": KID, "policy_version": version, "ttl_seconds": ttl_seconds,
        }, headers={"x-cloud-signer-token": "test-token"})
        assert sign.status_code == 201, sign.text
        bundle = sign.json()
    finally:
        _restore_env(saved)
    install = client.post("/api/v1/edge/policy/bundle/install", json={"bundle": bundle, "actor": "test-admin"})
    return install


def test_sign_requires_token_and_key(monkeypatch):
    resp = client.post("/api/v1/cloud/policy/bundles/sign", json={
        "payload": {"rules": {}}, "key_id": KID, "policy_version": 1,
    }, headers={"x-cloud-signer-token": "wrong"})
    assert resp.status_code == 503 or resp.status_code == 403


def test_full_sign_verify_install_flow():
    _register_key("staged")
    verify = client.get("/api/v1/edge/policy/keys").json()["keys"]
    assert any(k["kid"] == KID and k["role"] == "staged" for k in verify)

    install = _sign_and_install(version=100)
    assert install.status_code == 200, install.text
    body = install.json()
    assert body["policy_version"] == 100
    assert body["key_id"] == KID
    assert body["bundle"]["signature"]


def test_kid_must_be_trusted():
    bundle = {
        "bundle_id": "pol_test",
        "policy_version": 1001,
        "key_id": "unknown-key",
        "issued_at": "2026-11-17T14:00:00Z",
        "expires_at": "2026-11-18T14:00:00Z",
        "signature": base64.b64encode(b"fake").decode(),
    }
    resp = client.post("/api/v1/edge/policy/bundle/install", json={"bundle": bundle, "actor": "x"})
    assert resp.status_code == 409
    assert "unknown key_id" in resp.json()["detail"]


def test_tampered_signature_rejected():
    _register_key("staged")
    saved = _seed_env()
    try:
        sign = client.post("/api/v1/cloud/policy/bundles/sign", json={
            "payload": {"rules": {"max_auto_refund_cents": 1}}, "key_id": KID, "policy_version": 900,
        }, headers={"x-cloud-signer-token": "test-token"})
        bundle = sign.json()
    finally:
        _restore_env(saved)
    bundle["rules"] = {"max_auto_refund_cents": 99999999}
    resp = client.post("/api/v1/edge/policy/bundle/install", json={"bundle": bundle, "actor": "x"})
    assert resp.status_code == 409
    assert "signature" in resp.json()["detail"]


def test_version_must_be_monotonic():
    _register_key("staged")
    first = _sign_and_install(version=500)
    assert first.status_code == 200
    lower = _sign_and_install(version=400)
    assert lower.status_code == 409
    same = _sign_and_install(version=500)
    assert same.status_code == 409


def test_verifier_rejects_expired_bundle():
    _register_key("staged")
    saved = _seed_env()
    try:
        sign = client.post("/api/v1/cloud/policy/bundles/sign", json={
            "payload": {"rules": {}}, "key_id": KID, "policy_version": 600, "ttl_seconds": 60,
        }, headers={"x-cloud-signer-token": "test-token"})
        bundle = sign.json()
    finally:
        _restore_env(saved)
    # forge an earlier issued_at/expires_at in the past and re-sign with the same key
    bundle["issued_at"] = "2020-01-01T00:00:00Z"
    bundle["expires_at"] = "2020-01-02T00:00:00Z"
    unsigned = {k: v for k, v in bundle.items() if k != "signature"}
    raw = policy_signer.canonical_bundle_bytes(unsigned)
    bundle["signature"] = base64.b64encode(KEY.sign(raw)).decode()
    resp = client.post("/api/v1/edge/policy/bundle/install", json={"bundle": bundle, "actor": "x"})
    assert resp.status_code == 409
    assert "lease expired" in resp.json()["detail"]


def test_install_flips_hard_gate_to_bundle_verification():
    _register_key("staged")
    install = _sign_and_install(version=700, ttl_seconds=86400)
    assert install.status_code == 200

    resp = client.get("/api/v1/edge/status")
    assert resp.status_code == 200
    # The gate now uses the signed-bundle path; make the bundle stale and expect a closed gate
    old = settings.POLICY_BUNDLE_MAX_AGE_SECONDS
    settings.POLICY_BUNDLE_MAX_AGE_SECONDS = 0.0001
    try:
        resp = client.post("/api/v1/operations/adjustments",
                           json={"sku": f"GATE-{uuid.uuid4().hex[:6]}", "quantity_delta": 1, "actor": "ops-1"})
        assert resp.status_code == 409
    finally:
        settings.POLICY_BUNDLE_MAX_AGE_SECONDS = old


def test_retire_key_excludes_it_from_trust_ring():
    kid = f"retire-me-{uuid.uuid4().hex[:6]}"
    key = Ed25519PrivateKey.generate()
    pub = base64.urlsafe_b64encode(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
    client.post("/api/v1/edge/policy/keys", json={"kid": kid, "public_key_b64": pub, "role": "staged", "actor": "a"})
    resp = client.post(f"/api/v1/edge/policy/keys/{kid}/retire", json={"actor": "a"})
    assert resp.json()["role"] == "retired"
    ring = {k["kid"]: k for k in client.get("/api/v1/edge/policy/keys").json()["keys"]}
    assert ring[kid]["role"] == "retired"
