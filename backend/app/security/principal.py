"""Authenticated principal, RBAC and tenant/store scoping.

Two token modes:

- ``local``: HS256 tokens issued by :mod:`app.api.auth` from env-configured
  users (bcrypt password hashes). Intended for development and pilots.
- ``oidc``: RS256 tokens issued by the corporate IdP, verified against its
  JWKS with issuer and audience checks. This is the production mode.

Every permission decision is derived **only from verified token claims**.
Client-supplied ``actor`` / ``tenant_id`` values are never trusted for
authorization; they are validated against this principal where still present
for backwards compatibility with existing call sites.
"""

import time
from dataclasses import dataclass, field
from typing import Any, Iterable

import httpx
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from app.config import settings

_ALGORITHMS_RS = ("RS256",)
_bearer = HTTPBearer(auto_error=False)

# Separation of duties: approval and execution are intentionally distinct.
ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "operator": frozenset({"read:console", "sale:create"}),
    "store_manager": frozenset({"read:console", "sale:create", "recommendation:approve"}),
    "operations_manager": frozenset({"read:console", "sale:create", "recommendation:execute"}),
    "regional_manager": frozenset(
        {"read:console", "sale:create", "recommendation:approve", "recommendation:execute"}
    ),
    "system_admin": frozenset(
        {
            "read:console",
            "sale:create",
            "recommendation:approve",
            "recommendation:execute",
            "admin:*",
        }
    ),
}

ADMIN_PERMISSION = "admin:*"


@dataclass(frozen=True)
class Principal:
    subject: str
    role: str
    permissions: frozenset[str]
    tenants: frozenset[str] = field(default_factory=frozenset)
    stores: frozenset[str] = field(default_factory=frozenset)
    auth_mode: str = "local"
    claims: dict[str, Any] = field(default_factory=dict)

    def has(self, permission: str) -> bool:
        return permission in self.permissions or ADMIN_PERMISSION in self.permissions


def _as_str_set(value: Any) -> frozenset[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple, set)):
        return frozenset()
    return frozenset(str(v) for v in value if str(v))


_JWKS_CACHE: dict[str, Any] = {"keys": {}, "expires": 0.0}


def _fetch_jwks() -> dict[str, dict]:
    """Fetch and cache the IdP JWKS as kid -> raw JWK. Monkeypatchable for tests."""
    now = time.monotonic()
    cached = _JWKS_CACHE.get("keys") or {}
    if cached and _JWKS_CACHE.get("expires", 0.0) > now:
        return cached
    url = (settings.OIDC_JWKS_URL or "").strip()
    if not url:
        raise JWTError("OIDC_JWKS_URL is not configured")
    response = httpx.get(url, timeout=10.0)
    response.raise_for_status()
    keys = {str(jwk["kid"]): jwk for jwk in response.json().get("keys", []) if jwk.get("kid")}
    _JWKS_CACHE["keys"] = keys
    _JWKS_CACHE["expires"] = now + max(0, settings.OIDC_JWKS_CACHE_SECONDS)
    return keys


def _decode_oidc(token: str) -> dict[str, Any]:
    header = jwt.get_unverified_header(token)
    kid = str(header.get("kid") or "")
    keys = _fetch_jwks()
    key = keys.get(kid)
    if key is None:
        raise JWTError(f"untrusted or unknown kid: {kid!r}")
    audience = (settings.OIDC_AUDIENCE or "").strip() or None
    return jwt.decode(
        token,
        key=key,
        algorithms=list(_ALGORITHMS_RS),
        audience=audience,
        issuer=(settings.OIDC_ISSUER or "").strip() or None,
    )


def _decode_local(token: str) -> dict[str, Any]:
    return jwt.decode(
        token,
        settings.SECRET_KEY,
        algorithms=[settings.JWT_ALGORITHM],
        audience=settings.JWT_AUDIENCE,
        issuer=settings.JWT_ISSUER,
    )


def _unauthorized(detail: str = "Missing or invalid bearer token") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> Principal:
    """Verify the bearer token and build a minimized, verified principal."""
    if credentials is None or not credentials.credentials:
        raise _unauthorized("Missing bearer token")

    bearer_value = credentials.credentials
    mode = settings.AUTH_MODE.strip().lower()
    try:
        if mode == "oidc":
            claims = _decode_oidc(bearer_value)
            role = str(claims.get(settings.OIDC_ROLE_CLAIM) or "")
            tenants = _as_str_set(claims.get(settings.OIDC_TENANT_CLAIM))
            stores = _as_str_set(claims.get(settings.OIDC_STORE_CLAIM))
        elif mode == "local":
            claims = _decode_local(bearer_value)
            role = str(claims.get("role") or "")
            tenants = _as_str_set(claims.get("tenants"))
            stores = _as_str_set(claims.get("stores"))
        else:
            raise JWTError(f"unsupported AUTH_MODE {mode!r}")
    except (JWTError, ValueError, httpx.HTTPError) as exc:
        raise _unauthorized(f"Invalid or expired bearer token: {exc}")

    subject = str(claims.get("sub") or "").strip()
    if not subject:
        raise _unauthorized("Token is missing 'sub'")

    return Principal(
        subject=subject,
        role=role,
        permissions=ROLE_PERMISSIONS.get(role, frozenset()),
        tenants=tenants,
        stores=stores,
        auth_mode=mode,
        claims=claims,
    )


def require_permission(*permissions: str):
    """Dependency factory: require all named permissions."""

    def dependency(principal: Principal = Depends(get_current_principal)) -> Principal:
        missing = [p for p in permissions if not principal.has(p)]
        if missing:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Insufficient role: missing {', '.join(missing)}",
            )
        return principal

    return dependency


def require_tenant(principal: Principal, tenant_id: str) -> str:
    """A caller may only act on tenants present in its verified token."""
    if principal.has(ADMIN_PERMISSION):
        return tenant_id
    if not principal.tenants or tenant_id not in principal.tenants:
        raise HTTPException(status_code=403, detail="Tenant is outside the authenticated scope")
    return tenant_id


def require_store(principal: Principal, tenant_id: str, store_id: str) -> str:
    require_tenant(principal, tenant_id)
    if principal.has(ADMIN_PERMISSION):
        return store_id
    if not principal.stores or store_id not in principal.stores:
        raise HTTPException(status_code=403, detail="Store is outside the authenticated scope")
    return store_id


def require_actor(principal: Principal, requested: Iterable[str] | str | None) -> str:
    """The executing actor comes from the token, never from the request body."""
    if requested is None:
        return principal.subject
    candidates = [requested] if isinstance(requested, str) else list(requested)
    for candidate in candidates:
        if str(candidate) == principal.subject:
            return principal.subject
    raise HTTPException(status_code=403, detail="Actor does not match the authenticated principal")
