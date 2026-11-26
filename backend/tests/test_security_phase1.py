"""Phase 1 identity, authorization and transport defence tests.

These opt out of the shared authenticated fixture with
``@pytest.mark.no_auth_override`` so the real verification path is exercised.
"""

import base64
import json
import os
import tempfile
import uuid

import pytest
from fastapi.testclient import TestClient
from jose import jwt
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.api import auth as auth_api
from app.config import settings
from app.console.seed import seed_demo
from app.db.database import Base, get_db
from app.edge.policy import seed_default_policies
from app.main import app
from app.security import principal as principal_module
from app.security.auth import create_access_token, hash_password

client = TestClient(app)

AUTH = "/api/v1/auth"
CONSOLE = "/api/v1/console"
DEMO_REC = "rec_console_FL-VA-042"


@pytest.fixture
def seeded_db():
    path = os.path.join(tempfile.gettempdir(), f"vejit_sec_{uuid.uuid4().hex}.db")
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
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
    yield
    app.dependency_overrides.pop(get_db, None)


def _token(subject="alice", role="store_manager", tenants=None, stores=None) -> str:
    return create_access_token(
        subject=subject,
        role=role,
        tenants=["foodlion", "acehardware", "lowes"] if tenants is None else tenants,
        stores=["FL-VA-042", "ACE-NC-001", "LOW-NC-DC1"] if stores is None else stores,
    )


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.no_auth_override
def test_missing_token_is_401():
    resp = client.get(f"{CONSOLE}/catalog")
    assert resp.status_code == 401
    assert resp.headers["www-authenticate"] == "Bearer"


@pytest.mark.no_auth_override
def test_malformed_and_expired_tokens_are_401():
    assert client.get(f"{CONSOLE}/catalog", headers=_bearer("not.a.jwt")).status_code == 401
    expired = jwt.encode(
        {"sub": "alice", "role": "system_admin", "exp": 1,
         "iss": settings.JWT_ISSUER, "aud": settings.JWT_AUDIENCE},
        settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM,
    )
    assert client.get(f"{CONSOLE}/catalog", headers=_bearer(expired)).status_code == 401


@pytest.mark.no_auth_override
def test_local_login_from_env_users_and_me(monkeypatch):
    users = [{
        "username": "alice",
        "password_hash": hash_password("correct-horse"),
        "role": "store_manager",
        "tenants": ["foodlion"],
        "stores": ["FL-VA-042"],
    }]
    monkeypatch.setattr(settings, "LOCAL_USERS_JSON", json.dumps(users))
    monkeypatch.setattr(settings, "AUTH_MODE", "local")

    bad = client.post(f"{AUTH}/login", json={"username": "alice", "password": "wrong"})
    assert bad.status_code == 401

    login = client.post(f"{AUTH}/login", json={"username": "alice", "password": "correct-horse"})
    assert login.status_code == 200, login.text
    token = login.json()["access_token"]

    me = client.get(f"{AUTH}/me", headers=_bearer(token))
    assert me.status_code == 200, me.text
    body = me.json()
    assert body["subject"] == "alice"
    assert body["tenants"] == ["foodlion"]
    assert body["stores"] == ["FL-VA-042"]
    assert "recommendation:approve" in body["permissions"]
    assert "recommendation:execute" not in body["permissions"]


@pytest.mark.no_auth_override
def test_login_disabled_without_configured_users(monkeypatch):
    monkeypatch.setattr(settings, "LOCAL_USERS_JSON", "")
    monkeypatch.setattr(settings, "AUTH_MODE", "local")
    resp = client.post(f"{AUTH}/login", json={"username": "x", "password": "y"})
    assert resp.status_code == 503


@pytest.mark.no_auth_override
def test_no_credentials_hardcoded_in_source():
    source = open(auth_api.__file__, encoding="utf-8").read()
    for secret in ("exec123", "region123", "store123", "admin123"):
        assert secret not in source


@pytest.mark.no_auth_override
def test_tenant_scope_comes_from_token_not_query(monkeypatch):
    token = _token(subject="ace-user", tenants=["acehardware"], stores=["ACE-NC-001"])
    denied = client.get(
        f"{CONSOLE}/overview?tenant_id=foodlion&store_id=FL-VA-042", headers=_bearer(token)
    )
    assert denied.status_code == 403

    allowed = client.get(
        f"{CONSOLE}/overview?tenant_id=acehardware&store_id=ACE-NC-001", headers=_bearer(token)
    )
    assert allowed.status_code == 200, allowed.text


@pytest.mark.no_auth_override
def test_rbac_separates_approval_from_execution(seeded_db):
    token = _token(subject="alice", role="store_manager")
    approve = client.post(
        f"{CONSOLE}/recommendations/{DEMO_REC}/approve",
        json={"actor": "alice"}, headers=_bearer(token),
    )
    assert approve.status_code == 200, approve.text

    execute = client.post(
        f"{CONSOLE}/recommendations/{DEMO_REC}/execute",
        json={"actor": "alice"}, headers=_bearer(token),
    )
    assert execute.status_code == 403
    assert "recommendation:execute" in execute.json()["detail"]


@pytest.mark.no_auth_override
def test_actor_is_bound_to_token(seeded_db):
    token = _token(subject="alice", role="regional_manager")
    mismatched = client.post(
        f"{CONSOLE}/recommendations/{DEMO_REC}/approve",
        json={"actor": "someone-else"}, headers=_bearer(token),
    )
    assert mismatched.status_code == 403
    assert "Actor does not match" in mismatched.json()["detail"]

    matching = client.post(
        f"{CONSOLE}/recommendations/{DEMO_REC}/approve",
        json={"actor": "alice"}, headers=_bearer(token),
    )
    assert matching.status_code == 200, matching.text
    assert matching.json()["approved_by"] == "alice"


@pytest.mark.no_auth_override
def test_rate_limit_returns_429(monkeypatch):
    monkeypatch.setattr(settings, "RATE_LIMIT_PER_MINUTE", 3)
    monkeypatch.setattr(settings, "RATE_LIMIT_BURST", 3)
    token = _token(subject="rate-test-user")
    codes = [client.get(f"{CONSOLE}/catalog", headers=_bearer(token)).status_code for _ in range(5)]
    assert 200 in codes
    assert 429 in codes


@pytest.mark.no_auth_override
def test_csrf_rejects_foreign_origin_with_cookie(monkeypatch):
    monkeypatch.setattr(settings, "CSRF_ENFORCE", True)
    monkeypatch.setattr(settings, "ALLOWED_ORIGINS", ["https://console.example.com"])
    token = _token(subject="csrf-user")

    foreign = client.post(
        f"{CONSOLE}/orders",
        json={"tenant_id": "foodlion", "store_id": "FL-VA-042", "lines": []},
        headers={**_bearer(token), "Origin": "https://evil.example.com", "Cookie": "session=x"},
    )
    assert foreign.status_code == 403
    assert "CSRF" in foreign.json()["detail"]

    same = client.post(
        f"{CONSOLE}/orders",
        json={"tenant_id": "foodlion", "store_id": "FL-VA-042", "lines": []},
        headers={**_bearer(token), "Origin": "https://console.example.com", "Cookie": "session=x"},
    )
    assert same.status_code != 403


def _rsa_jwk(kid="test-kid"):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    numbers = key.public_key().public_numbers()

    def b64(n: int) -> str:
        return base64.urlsafe_b64encode(n.to_bytes((n.bit_length() + 7) // 8, "big")).decode().rstrip("=")

    jwk = {"kty": "RSA", "kid": kid, "use": "sig", "alg": "RS256", "n": b64(numbers.n), "e": b64(numbers.e)}
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    return jwk, pem


@pytest.mark.no_auth_override
def test_oidc_jwks_verification_and_claim_mapping(monkeypatch):
    jwk, pem = _rsa_jwk()
    monkeypatch.setattr(settings, "AUTH_MODE", "oidc")
    monkeypatch.setattr(settings, "OIDC_ISSUER", "https://idp.example.com")
    monkeypatch.setattr(settings, "OIDC_AUDIENCE", "ve-jit-api")
    monkeypatch.setattr(principal_module, "_fetch_jwks", lambda: {jwk["kid"]: jwk})

    token = jwt.encode(
        {
            "sub": "oidc-user", "role": "regional_manager",
            "vejit_tenants": ["lowes"], "vejit_stores": ["LOW-NC-DC1"],
            "iss": "https://idp.example.com", "aud": "ve-jit-api", "exp": 9_999_999_999,
        },
        pem, algorithm="RS256", headers={"kid": jwk["kid"]},
    )
    me = client.get(f"{AUTH}/me", headers=_bearer(token))
    assert me.status_code == 200, me.text
    assert me.json()["subject"] == "oidc-user"
    assert me.json()["tenants"] == ["lowes"]
    assert me.json()["auth_mode"] == "oidc"

    wrong_aud = jwt.encode(
        {"sub": "oidc-user", "role": "regional_manager", "iss": "https://idp.example.com",
         "aud": "someone-else", "exp": 9_999_999_999},
        pem, algorithm="RS256", headers={"kid": jwk["kid"]},
    )
    assert client.get(f"{AUTH}/me", headers=_bearer(wrong_aud)).status_code == 401

    untrusted = jwt.encode(
        {"sub": "oidc-user", "role": "regional_manager", "iss": "https://idp.example.com",
         "aud": "ve-jit-api", "exp": 9_999_999_999},
        pem, algorithm="RS256", headers={"kid": "unknown-kid"},
    )
    assert client.get(f"{AUTH}/me", headers=_bearer(untrusted)).status_code == 401
