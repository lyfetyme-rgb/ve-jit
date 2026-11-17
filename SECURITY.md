# Security Policy

Report vulnerabilities privately to: security@vejit.com

## Production checklist
- Replace demo secrets in `.env`
- Enable TLS at ingress / load balancer
- Restrict CORS origins
- Use managed PostgreSQL and Redis in production
- Enable secret scanning and dependency updates

## Three planes and least privilege
- Intelligence endpoints never mutate business state; only `PROPOSED`
  recommendations can be written there. Actions require Operations plus an
  `APPROVED` recommendation (`/api/v1/operations/recommendations/{uid}/execute`).
- Bind the actor/approver fields to the SSO identity at the edge (SSO/OIDC claims);
  do not trust client-supplied actor strings in an internet-facing deployment.
- Enforce segregation of duties: the approver of a purchase order must not be
  its creator (403), and a recommendation approver is recorded alongside the
  executing actor for audit.
- POS devices integrate via `direct-post` on a trusted network; use signed
  terminal certificates or a gateway device before exposing the endpoint.
- Audit middleware records every request to `audit_logs/audit.log`; mutation
  endpoints also persist actors on the affected rows (ledger entries carry
  `actor`, orders carry register id, recommendations carry approver/executor).

## Edge node (Vendarix)
- Edge policy bundles are Ed25519-signed with a trust ring; the edge rejects untrusted `key_id`s,
  tampered signatures and non-monotonic versions (fail closed). The hard safety gate flips to
  bundle verification once a signed bundle is installed.
- Cloud ingestion (`POST /api/v1/cloud/recovery/sessions`) verifies hash-chain continuity against
  the per-store watermark tip; any gap or forgery rejects the batch (409). Idempotent dedup prevents
  double-apply of replays.
- Non-commutative or unverifiable batches route to the HITL quarantine queue with machine reasons;
  nothing is silently accepted and nothing mutates cloud inventory without a commutative verdict
  or a named-human `apply`.
- Two-step policy gate: hard boolean invariants (not SAFE_MODE, fresh policy
  bundle, valid credentials) are checked before any threshold rule — an
  invalid bundle denies actions even in ONLINE.
- Event store and audit ledger are append-only (ORM hooks + DB triggers in
  `infrastructure/database/append_only.sql`); operational projections use
  compare-and-swap version columns (409 on conflict).
- Every event carries tenant/store/device identity, a unique idempotency key
  (unique constraint) and a SHA-256 payload hash — tampering or replay without
  the original key is detectable and replays return the original event.
- Deterministic policy HOLDs (unknown device, stale heartbeat, unrecognized
  payload, weight mismatch, quantity threshold) place actions on the human
  authorization queue; no policy can be overridden by model output.
- SAFE_MODE is a hard stop: unsafe action classes (inventory moves, receiving,
  POs, transfers, waste, price/promotion changes, skill execution) return 409
  regardless of continuity value, risk score or credentials. Recovery is an
  explicit `RECOVERING` transition followed by verify → replay → resolve → ack.
- AI skills execute as isolated subprocesses with no database handles; only
  skills whose config marks `approved=true` may run.
- The edge holds no cloud credentials. Cloud synchronization is an explicit,
  configured operation (`CLOUD_SYNC_URL` + `CLOUD_SYNC_TOKEN`); without them,
  outbox drains and cloud ack are dry-run or require an explicit human
  `ack_mode=local_override`.
- Adapter payloads are untrusted input; unknown payloads are quarantined as
  `edge.unrecognized` with the payload hash preserved, never guessed.
