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
