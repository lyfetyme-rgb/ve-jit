# Production Readiness

This document tracks the four phases from the readiness review.

## Phase 1 — Identity and access ✅ (code complete)

Bearer auth (local HS256 / OIDC JWKS RS256), RBAC with approval/execution
separation, token-derived tenant/store scoping, token-bound actor, CSRF origin
checks, rate limiting. See `docs/IDENTITY_AUTHORIZATION.md`.

## Phase 2 — Infrastructure ✅ (code complete; deployment is yours)

- Secrets can be supplied via `*_FILE` mounts (`SECRET_KEY_FILE`,
  `POLICY_SIGNING_KEY_FILE`, `PAYMENT_PSP_TOKEN_FILE`, ...).
- `RATE_LIMIT_BACKEND=redis` gives a shared limiter across replicas; `memory`
  is the development fallback.
- `infrastructure/kubernetes/production/` overlay: 3 replicas, rolling update,
  non-root security context, resource limits, probes, ConfigMap and Secret
  references. **Set `DATABASE_URL` to managed PostgreSQL** — the base manifest's
  SQLite value is not valid with multiple replicas.
- Optional Postgres driver: `backend/requirements-postgres.txt`.

## Phase 3 — Integrations ✅ (boundary complete; vendors are external)

- Retailer loading is config-driven: `config/retailers.demo.json`,
  `RETAILER_CONFIG`, `tools/load_retailers.py`. No retailer names in code.
- Payment authorization boundary: `app/integrations/payments.py` with a no-op
  demo adapter and a tokenized PSP adapter. Cardholder data is rejected before
  parsing; only an opaque token is forwarded.
- The C++/Go sidecars and the verified recovery flow are unchanged.

## Phase 4 — Assurance and rollout ✅ (artifacts complete; certification external)

- Load harness: `tools/load_test.py` (p50/p95/p99, rps, errors).
- Alerts: `infrastructure/monitoring/alerts.yaml`.
- Runbook: `docs/RUNBOOK.md`; staged rollout: `docs/ROLLOUT_PLAN.md`.
- Honest scope gap list: `docs/PCI_GAP_ASSESSMENT.md`.

## Still external (cannot be completed in code)

| Item | Owner |
|---|---|
| Managed Postgres, Redis, vault and TLS ingress | Your platform team |
| OIDC tenant configuration | Your IdP admin |
| PCI DSS assessment / QSA | Qualified assessor |
| Retailer API contracts and trademark permission | Legal / vendor relations |
| PSP merchant account and credentials | Your payment team |
| Hardware/EMV certification | Device vendor |

## Verification gates

- `python -m pytest` — full backend suite
- `python tools/scan_secrets.py` — must report PASS
- `python tools/load_retailers.py <catalog> --db <url>` — idempotent load
- `python tools/load_test.py --url <base> --duration 30 --concurrency 10`
- Four CI checks on every PR: `test`, `sync-go`, `hardware-adapter`, `typescript`
