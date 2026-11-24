# Jira + JSM + Confluence Automation — Three Loops

The VE-JIT backend owns **all** Atlassian integration. Edge nodes never call
Atlassian APIs and never hold tokens; they only POST health events to the
central backend. This centralizes batching, storm deduplication and the
rate limit so thousands of stores can check in safely.

```
stores ──health──▶ ve-jit central ──outbox + token bucket──▶ Jira / JSM / Confluence
                        ▲
       Jira change webhook ─ unlock release gate
```

## Loop 1 — Release & Audit (Jira Software ↔ ve-jit → Confluence)

1. A Change Request in `JIRA_PROJECT_KEY` moves to **Approved**. Jira sends
   `POST /api/v1/integrations/jira/change-approved` with an HMAC-SHA256 header
   `x-hub-signature-256` (secret file `ATLASSIAN_WEBHOOK_SECRET_FILE`).
2. The webhook is signature-checked and idempotent; it unlocks
   `GET /api/v1/integrations/jira/release-gate/{issue_key}` and enqueues an audit comment.
3. On a green release run, `POST /api/v1/integrations/release/audit` publishes a
   Confluence page containing the version, `manifest.json` hash, `SHA256SUMS.txt`
   hash and linked Jira issues, and writes an append-only `int_release_audits`
   record with the page hash. Any later edit breaks the hash.

## Loop 2 — Store Self-Healing (edge → JSM → Confluence)

1. `POST /api/v1/integrations/edge/health` from the edge agent with
   `store_id`, `architecture` (`amd64`|`arm64`), `version`, `healthy` and a reason.
2. On failure, exactly one JSM incident per `(tenant, store, architecture)` is
   opened through the rate-limited outbox, with the runbook page ID. Repeats
   return `ALREADY_OPEN` — storm alerts are deduplicated, never one-per-check.
3. After rollback and a passing health check (which must carry the root-cause
   log hash), the incident is transitioned to **Resolved** with that digest
   and a root-cause note.

## Loop 3 — Rate-limit & outage resilience

- `TokenBucket(rate_per_min=ATLASSIAN_RATE_LIMIT_PER_MIN, burst=ATLASSIAN_BURST)` gates
  every outbound call; a 429/5xx triggers bounded exponential retry.
- `int_atlassian_outbox` gives at-least-once delivery with unique
  `idempotency_key`, `PENDING → DELIVERED/FAILED/FAILED_DLQ`. Drain order is
  **priority lanes**: P1 critical incidents first, then P3 info/telemetry, then
  P4 Confluence page updates — a 2,000-item telemetry backlog cannot starve a
  P1 cold-chain/POS-down alert. Permanent 4xx (except 429) go to `FAILED_DLQ`
  with the full payload retained; the dashboard shows a mapping-drift banner
  and an admin can requeue after fixing the Jira/JSM mapping.
- With `ATLASSIAN_ENABLED=false` the `DryRunAtlassianClient` records operations
  locally: all tests and local dev run without network or credentials.

## Configuration (central backend ONLY)

| Setting | Notes |
|---|---|
| `ATLASSIAN_ENABLED` | false by default; dry-run otherwise |
| `ATLASSIAN_SITE_URL`, `ATLASSIAN_CLOUD_ID` | site identity |
| `ATLASSIAN_EMAIL`, `ATLASSIAN_API_TOKEN_FILE` | token from a secret file, never source |
| `ATLASSIAN_WEBHOOK_SECRET_FILE` | Jira webhook HMAC secret |
| `JIRA_PROJECT_KEY`, `JSM_SERVICE_DESK_ID`, `CONFLUENCE_SPACE_KEY`, `CONFLUENCE_ROOT_PAGE_ID` | project/runbook mapping |
| `ATLASSIAN_RATE_LIMIT_PER_MIN`, `ATLASSIAN_BURST`, `ATLASSIAN_MAX_RETRIES`, `ATLASSIAN_STORM_WINDOW_SECONDS` | resilience knobs |

## Boundaries

- No edge code imports `app.integrations` or holds Atlassian credentials —
  asserted by `tests/test_atlassian_loops.py::test_edge_modules_never_import_atlassian`.
- BENGAL BAA remains observation-only; it never opens or resolves incidents.
- Confluence "immutability" is enforced by the page hash + local append-only
  audit record; Confluence itself is editable, so verify the hash before trusting it.
