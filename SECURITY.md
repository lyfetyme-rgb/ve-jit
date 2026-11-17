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
- The edge holds no cloud credentials. Cloud synchronization is an explicit,
  configured operation (`CLOUD_SYNC_URL`); without it, outbox drains are dry-run.
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
- Adapter payloads are untrusted input; unknown payloads are quarantined as
  `edge.unrecognized` with the payload hash preserved, never guessed.
