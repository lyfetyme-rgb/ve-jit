# VE-JIT Operations Runbook

Scope: the central control plane (FastAPI) and the store edge nodes. Store
nodes are deployed from the released `.deb` / `.tar.gz` bundles via systemd;
they are never deployed to Vercel or Kubernetes.

## Daily checks

1. `GET /health/live` and `/health/ready` — both `200`.
2. `GET /api/v1/console/catalog` with a valid bearer token — tenant/store registry loads.
3. `GET /api/v1/edge/status` — continuity state is not `SAFE_MODE`.
4. `GET /api/v1/integrations/outbox/status` — no unexpected DLQ entries.
5. Alert dashboard from `infrastructure/monitoring/alerts.yaml` is clear.

## Backend down

- Check `kubectl -n vejit-platform get pods` and describe the failing pod.
- Confirm the `vejit-secrets` Secret exists and `DATABASE_URL` points to the
  managed Postgres instance (multi-replica + SQLite is unsupported).
- Confirm `/health/live` and `/health/ready` paths are unchanged.
- Roll back with `kubectl -n vejit-platform rollout undo deployment/vejit-backend`.
- Never bypass the policy-safety gate to "restore service" — fail closed is correct.

## High error rate

- Correlate 5xx with authorization failures (401/403) and payment adapter errors.
- If `PAYMENT_ADAPTER=tokenized`, verify `PAYMENT_PSP_URL` and the PSP token file.
- Check Postgres connection pool saturation and Redis availability.
- If a release correlates, roll back rather than editing policy rules.

## Rate-limit storm

- With `RATE_LIMIT_BACKEND=redis`, verify Redis is healthy and that the
  `vejit:rl:*` keys are not growing without TTL.
- Identify the caller by the token hash in the 429 response path (do not log raw tokens).
- Adjust `RATE_LIMIT_PER_MINUTE` / `RATE_LIMIT_BURST` via ConfigMap only after
  confirming the traffic is legitimate.

## Outbox backlog

- The Atlassian outbox is **central-only**. Store nodes must never call Atlassian.
- Check the central drain worker and the DLQ banner in the dashboard.
- Permanent 4xx items belong in the DLQ with a dashboard banner — do not delete them.
- P1 critical incidents take priority over P3 telemetry and P4 Confluence items.

## Edge node (store) incident

1. `systemctl status vendarix-edge` on the store machine.
2. Check continuity state via `GET /api/v1/edge/status`; `SAFE_MODE` blocks actions by design.
3. Verify the signed policy bundle is fresh (`POLICY_BUNDLE_MAX_AGE_SECONDS`);
   a stale bundle correctly closes the hard safety gate. Install a freshly signed
   bundle through the normal distribution channel — never disable the gate.
4. Use the verified recovery flow (`verify → replay → resolve → ack`); never
   hand-edit the append-only ledger or event store.

## Secret rotation

- `SECRET_KEY`, signing keys and PSP tokens are supplied via `*_FILE` mounts
  from the vault; rotate in the vault and restart pods to pick up new files.
- Policy signing key rotation uses the active/staged/retired trust ring —
  publish next key, cut over signing, then retire the old key after expiry.
- Never commit secrets to the repository; `tools/scan_secrets.py` runs in CI.

## Backup and restore

- Managed Postgres: enable point-in-time recovery; test restore quarterly.
- Event store and audit ledger are append-only — include them in backups.
- Restore procedure: restore DB, verify append-only integrity registration,
  verify policy bundle, then restart services. Do not replay from an unverified watermark.

## Escalation

- Security/governance issue: stop writes, preserve logs, escalate to the owner.
- PCI-scope question: pause the change and consult a qualified assessor.
