# Identity, Authorization & Transport Defences (Phase 1)

Every API route except `/health` and `/api/v1/auth/login` now requires a
verified bearer token. The old hardcoded demo passwords were removed.

## Modes

### `local` (development / pilot)

Set `AUTH_MODE=local`. Users come from `LOCAL_USERS_JSON` — a JSON array of:

```json
[
  {
    "username": "alice",
    "password_hash": "pbkdf2_sha256$310000$<salt>$<hash>",
    "role": "store_manager",
    "tenants": ["foodlion"],
    "stores": ["FL-VA-042"]
  }
]
```

Generate an entry without putting the password in shell history or source:

```bash
python tools/gen_local_user.py alice store_manager foodlion --stores FL-VA-042
```

The tool prompts for the password and prints the JSON entry. Store the result
in your secret manager / environment — **never commit it**. With no users
configured, `/login` returns 503 (it does not fall back to demo credentials).

Tokens are HS256, signed with `SECRET_KEY`, carrying `iss`, `aud`, `exp`,
`role`, `tenants` and `stores`. Set a long random `SECRET_KEY` in production.

### `oidc` (production)

Set:

```
AUTH_MODE=oidc
OIDC_ISSUER=https://idp.example.com
OIDC_AUDIENCE=ve-jit-api
OIDC_JWKS_URL=https://idp.example.com/.well-known/jwks.json
OIDC_ROLE_CLAIM=role
OIDC_TENANT_CLAIM=vejit_tenants
OIDC_STORE_CLAIM=vejit_stores
```

Tokens must be RS256, include a `kid` present in the JWKS, and match the
configured issuer and audience. JWKS responses are cached for
`OIDC_JWKS_CACHE_SECONDS`. Sign-in is handled by the IdP; VE-JIT only verifies.

## Roles → permissions

| Role | Permissions |
|---|---|
| `operator` | `read:console`, `sale:create` |
| `store_manager` | + `recommendation:approve` |
| `operations_manager` | + `recommendation:execute` |
| `regional_manager` | approve **and** execute |
| `system_admin` | all of the above + `admin:*` (break-glass; audited) |

Approval and execution are deliberately separated: a `store_manager` can
approve but cannot execute. This enforces the three-plane boundary with an
identity boundary, not just a workflow convention.

Unknown roles receive **no** permissions (fail closed).

## Tenant / store scoping

`tenant_id` and `store_id` query parameters still exist for compatibility, but
they are validated against the verified token's `tenants` / `stores` claims.
A caller cannot read another tenant by changing a query parameter. `admin:*`
bypasses scoping and should be tightly controlled.

The `actor` field in approve/execute request bodies is now checked against
`token.sub` and the recorded actor is always `token.sub` — a client can no
longer attribute an action to someone else.

## Transport defences

- **CSRF** (`CSRFMiddleware`): unsafe methods carrying a `Cookie` must present
  an `Origin` in `ALLOWED_ORIGINS`. Bearer-only clients are unaffected. Set
  `CSRF_ENFORCE=false` only for local development.
- **Rate limiting** (`RateLimitMiddleware`): sliding 60s window plus a 10s
  burst guard, keyed by token hash (or client IP). Login has its own lower
  bucket. **The store is in-process** — use a shared Redis-backed limiter when
  running multiple replicas.

## Endpoints

| Route | Auth |
|---|---|
| `POST /api/v1/auth/login` | Public (local mode only); rate-limited |
| `GET /api/v1/auth/me` | Bearer |
| All other `/api/v1/*` | Bearer, enforced at router level |
| `/health`, `/ui/*`, `/console/*` | Public (static assets / probes) |

## Verification

```bash
cd backend
python -m pytest tests/test_security_phase1.py -q   # 11 auth/RBAC/OIDC/CSRF/rate-limit tests
python -m pytest -q                                 # 115 total
```

## Still required for production

This phase closes the identity gap. It does **not** by itself deliver a
distributed rate limiter, secret rotation/HSM integration, or an IdP
configuration — those are deployment responsibilities documented above.
See the full production-readiness list in the project README / release notes.
