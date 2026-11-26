# Staged Rollout Plan

Never roll out the central plane and the store edge at the same time.

## Stage 0 — prerequisites (blocking)

- [ ] Managed PostgreSQL configured as `DATABASE_URL` (no SQLite with replicas).
- [ ] Redis reachable for shared rate limiting (`RATE_LIMIT_BACKEND=redis`).
- [ ] Secrets created in the vault and exposed as `*_FILE` mounts.
- [ ] OIDC issuer, audience and JWKS URL configured; `AUTH_MODE=oidc`.
- [ ] Retailer catalog loaded through `RETAILER_CONFIG` / `tools/load_retailers.py`.
- [ ] Payment adapter configured (`PAYMENT_ADAPTER=tokenized`) or explicitly `none` for a non-pilot.
- [ ] Alerts from `infrastructure/monitoring/alerts.yaml` wired to on-call.
- [ ] Load test run (`tools/load_test.py`) with agreed latency/error thresholds.

## Stage 1 — shadow (one central replica)

- Deploy one replica with read-only traffic mirrored; no store node changes.
- Verify auth, scoping, outbox and PCI guard with production-like tokens.
- Duration: minimum 3 business days with zero unexplained 4xx/5xx.

## Stage 2 — pilot store (1 store)

- Install the signed release bundle on one store machine via systemd.
- Keep the store's existing process as the fallback; run VE-JIT in observe mode first.
- Enable governed actions only after the signed policy bundle verifies and the
  continuity state is not `SAFE_MODE`.
- Verify every recommend → approve → execute → audit trail entry.

## Stage 3 — small cluster (3–5 stores)

- Enable distributed rate limiting and confirm 429 behavior under load.
- Exercise recovery: disconnect a store, queue events, reconnect, verify →
  replay → resolve → ack. Confirm dedup and no ledger corruption.
- Exercise the Atlassian loops end-to-end with a sandbox site.

## Stage 4 — chain rollout

- Roll out by region, one region at a time, with a 24h soak between regions.
- Keep the previous release bundle available for rollback.
- Monitor the four alerts in `alerts.yaml` and the DLQ banner continuously.

## Rollback

- Central: `kubectl rollout undo deployment/vejit-backend`.
- Store: reinstall the previous signed `.deb`, restart `vendarix-edge`, and
  verify the policy bundle. Never roll back by weakening a safety gate.
- Database changes are forward-only; roll back code, not schema.

## Go/no-go gate

Promote to the next stage only when: no critical alerts, no DLQ growth, all
authorized actions have matching audit entries, and the hard safety gates have
never been manually bypassed.
