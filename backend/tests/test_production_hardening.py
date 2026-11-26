"""Phase 2-4 hardening: secret files, payment boundary, Redis fallback, deployment artifacts."""

import json
import os
import tempfile
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import Settings, settings
from app.db.database import Base, get_db
from app.edge.policy import seed_default_policies
from app.integrations import payments
from app.console.seed import seed_demo
from app.main import app
from app.security.middleware import RedisRateLimitStore

client = TestClient(app)


def test_secret_can_be_supplied_from_file(monkeypatch):
    path = os.path.join(tempfile.gettempdir(), f"vejit_secret_{uuid.uuid4().hex}.txt")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("file-provided-secret-value\n")
    monkeypatch.setenv("SECRET_KEY_FILE", path)
    loaded = Settings()
    assert loaded.SECRET_KEY == "file-provided-secret-value"
    os.unlink(path)


def test_missing_secret_file_fails_closed(monkeypatch):
    monkeypatch.setenv("SECRET_KEY_FILE", "/nonexistent/vejit/secret")
    with pytest.raises(ValueError, match="Cannot read secret file"):
        Settings()


def test_noop_payment_adapter_is_deterministic(monkeypatch):
    monkeypatch.setattr(settings, "PAYMENT_ADAPTER", "none")
    adapter = payments.current_adapter()
    a = adapter.authorize(reference="pos-1", amount=10.0, store_id="GR-VA-042")
    b = adapter.authorize(reference="pos-1", amount=10.0, store_id="GR-VA-042")
    assert a == b and a.startswith("demo-auth-")


def test_tokenized_adapter_sends_only_opaque_token(monkeypatch):
    captured = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"authorization_id": "psp-auth-123"}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured.update(url=url, json=json, headers=headers)
        return FakeResponse()

    monkeypatch.setattr(settings, "PAYMENT_ADAPTER", "tokenized")
    monkeypatch.setattr(settings, "PAYMENT_PSP_URL", "https://psp.example.com/authorize")
    monkeypatch.setattr(settings, "PAYMENT_PSP_TOKEN", "psp-secret-token")
    monkeypatch.setattr(payments.httpx, "post", fake_post)

    ref = payments.current_adapter().authorize(
        reference="tok_opaque_123", amount=12.5, store_id="HW-NC-001"
    )
    assert ref == "psp-auth-123"
    assert captured["headers"]["Authorization"] == "Bearer psp-secret-token"
    assert captured["json"]["token"] == "tok_opaque_123"
    assert "card_number" not in captured["json"]


def test_redis_store_fails_open_when_unavailable():
    store = RedisRateLimitStore("redis://127.0.0.1:1/0")
    import asyncio

    allowed, retry = asyncio.run(store.allow("test-key", 1, 1))
    assert allowed is True and retry == 0


def test_sale_response_includes_payment_authorization():
    path = os.path.join(tempfile.gettempdir(), f"vejit_hardening_{uuid.uuid4().hex}.db")
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)
    db = session()
    seed_default_policies(db)
    seed_demo(db)
    db.close()

    def override():
        db = session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override
    try:
        response = client.post(
            "/api/v1/console/orders",
            json={
                "tenant_id": "grocery_demo",
                "store_id": "GR-VA-042",
                "lines": [{"sku": "GR-MILK-1G", "quantity": 1}],
                "payment_token": "tok_opaque_123",
                "client_order_id": "hardening-test-1",
            },
        )
        assert response.status_code == 201, response.text
        assert response.json()["authorization"].startswith("demo-auth-")
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_production_deployment_artifacts_are_valid():
    yaml = pytest.importorskip("yaml")
    root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    k8s = os.path.join(root, "infrastructure", "kubernetes", "production")
    for name in ("configmap.yaml", "secret-template.yaml", "backend-patch.yaml", "kustomization.yaml"):
        with open(os.path.join(k8s, name), encoding="utf-8") as handle:
            assert yaml.safe_load(handle)
    alerts = os.path.join(root, "infrastructure", "monitoring", "alerts.yaml")
    with open(alerts, encoding="utf-8") as handle:
        rules = yaml.safe_load(handle)
    assert rules["spec"]["groups"][0]["rules"]
    with open(os.path.join(root, "config", "retailers.demo.json"), encoding="utf-8") as handle:
        assert len(json.load(handle)["retailers"]) == 3
