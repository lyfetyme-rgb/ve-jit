# VE-JIT Enterprise Repository

VE-JIT is a production-oriented starter platform for retail and food-chain inventory optimization,
queue management, procurement automation, and supply-chain visibility.

## Included
- FastAPI backend with health, inventory, queue, IoT, analytics, and JIT demo endpoints
- **Three planes over one trusted data layer** — Commerce (POS/eCommerce transactions, pricing, promotions, loyalty),
  Operations (append-only inventory ledger, procurement, receiving, DC transfers, supplier terms, vendor collaboration),
  Intelligence (demand forecasting, anomaly detection, shelf availability, workforce guidance, supplier scorecards,
  waste analysis) with the governance rule: Intelligence produces recommendations, Operations executes
  authorized business actions, Commerce owns the customer-facing transaction. See `docs/THREE_PLANES.md`
- **Vendarix edge runtime** — the operational engine: hardware adapters (NCR Voyix, Toshiba TCGS JavaPOS/UPOS,
  Diebold Nixdorf, scanner-scale), shared event contracts with payload-hash integrity, deterministic policy gates with
  human authorization requests, a continuity state machine (ONLINE/DEGRADED/OFFLINE/SAFE_MODE) with an honest continuity
  value, transactional outbox + explicit verify→replay→resolve→ack recovery, and approved local AI skills under `skills/`.
  See `docs/EDGE_ARCHITECTURE.md`
- **Signed policy bundles** — Ed25519-signed, JWKS-style `key_id` + monotonic `policy_version` bundles with a
  staged→active→retired trust ring (`tools/gen_policy_bundle_key.py`); hard safety gate prefers verified bundles over
  local rules
- **Cloud recovery ingestion** — hash-chain continuity vs per-store watermark tip, idempotent dedup ledger,
  quarantine-first HITL reconciliation (`POST /api/v1/cloud/recovery/sessions`), payload-CAS apply
- **Compliance posture** — declared PCI-DSS out-of-scope boundary: non-PCI SKU/inventory telemetry only, HTTP 422 enforcement at the API edge (Luhn detector + card-named fields), redacting audit middleware, `GET /api/v1/compliance/pci-scope` machine-readable posture. See `docs/PCI_SCOPE.md`
- **Atlassian automation** — three loops: Jira-approved change gate + Confluence release audit, store self-healing JSM incidents with storm dedupe, central-only rate-limited outbox (edge never calls Atlassian). See `docs/ATLASSIAN_LOOPS.md`
- **BENGAL BAA assurance** — independent, observation-only threat-mode analyzer (bias, hallucination, safe information flow, poisoned sources) with a fixed 16-case T&E corpus; redacted reports only, no authorization/execution path. See `docs/BENGAL_BAA.md`
- **Release manifests** — `releases/<tag>/manifest.json` attaches to every GitHub Release automatically: the SKU → release mapping with per-file SHA256 for both platform bundles (see `releases/README.md`)
- **Migrations** — `infrastructure/migrations/` forward-only numbered SQL migrations (0000 baseline, 0001 append-only triggers, 0002 cloud reconciliation) with an idempotent dependency-free runner (`--status`, `--baseline`, `--db`)
- **Edge sidecars** — `services/hardware_adapter/`: C++ UDS daemon (systemd-sandboxed) for SDK-side bridges; `services/sync-go/`: Go replay classifier (commutative sales vs quarantine) for the recovery gate's replay step. Both are stateless; the Python edge is the single owner of local state, policy and audit. See `services/README.md`
- HTML dashboard UI served by FastAPI (includes a live Three Planes panel, PCI posture card and read-only BENGAL BAA assurance card)
- PostgreSQL + Redis + Kafka + Prometheus + Grafana Docker stack
- Tests, CI workflow, verification and deployment scripts
- Kubernetes starter manifests

## Releases

Tagged releases (`git tag v0.1.0 && git push origin v0.1.0`) build and publish
per-arch `.deb` + `.tar.gz` bundles with SHA256 checksums to GitHub Releases.
See [`docs/RELEASE.md`](docs/RELEASE.md) for the bundle layout, gates and the
one-command store installer.

## Three planes (Commerce / Operations / Intelligence)

The platform splits responsibilities across three planes sharing one trusted
data layer — see [`docs/THREE_PLANES.md`](docs/THREE_PLANES.md) for the full
contract, and [`docs/EDGE_ARCHITECTURE.md`](docs/EDGE_ARCHITECTURE.md) for the
edge node that feeds them. Quick tour:

```bash
# Edge: continuity state machine + event fabric
curl http://localhost:8000/api/v1/edge/status

# Ingest an NCR Voyix POS event (policy may HOLD; humans authorize)
curl -X POST http://localhost:8000/api/v1/edge/ingest \
  -H 'Content-Type: application/json' \
  -d '{"vendor":"ncr-voyix","device_id":"ncr-lane-1","source_seq":1,
       "raw":{"msgType":"TX_COMPLETE","txnId":"T-100","lines":[{"sku":"DAIRY-001","qty":2,"price":3.5}],"total":7.0}}'

# Recovery after connectivity returns is explicit (four ordered steps)
curl -X POST http://localhost:8000/api/v1/edge/continuity/transition \
  -H 'Content-Type: application/json' -d '{"to_state":"RECOVERING","actor":"ops-1","reason":"back online"}'
for step in connectivity-check hash-verification watermark-replay; do
  curl -X POST "http://localhost:8000/api/v1/edge/recovery/$step" \
    -H 'Content-Type: application/json' -d '{"actor":"ops-1"}'
done
curl -X POST http://localhost:8000/api/v1/edge/recovery/cloud-ack \
  -H 'Content-Type: application/json' -d '{"actor":"ops-1","resolution":"accept_local","ack_mode":"local_override"}'
```

```bash
# Plane registry + governance rule
curl http://localhost:8000/api/v1/planes

# Price a SKU (price book is authoritative; promotions apply automatically)
curl -X POST http://localhost:8000/api/v1/commerce/prices \
  -H 'Content-Type: application/json' -d '{"sku":"DAIRY-001","unit_price":6.5}'

# POST a POS transaction (idempotent on client_order_id)
curl -X POST http://localhost:8000/api/v1/commerce/pos/transactions \
  -H 'Content-Type: application/json' \
  -d '{"register_id":"lane-1","payment_method":"card","lines":[{"sku":"DAIRY-001","quantity":2}]}'

# Intelligence recommends; it cannot execute
curl http://localhost:8000/api/v1/intelligence/anomalies

# Human approves a recommendation, then Operations executes it
curl -X POST http://localhost:8000/api/v1/intelligence/recommendations/<uid>/approve \
  -H 'Content-Type: application/json' -d '{"approver":"manager-1"}'
curl -X POST http://localhost:8000/api/v1/operations/recommendations/<uid>/execute \
  -H 'Content-Type: application/json' -d '{"actor":"ops-1"}'
```

## Quick start

### Docker
```bash
docker compose up -d --build
```

Backend API:
- http://localhost:8000/docs
- http://localhost:8000/health

Dashboard UI:
- http://localhost:8000/ui/

### Local dev
```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
uvicorn app.main:app --reload
```

## Important
This repository is a full runnable enterprise-grade starter, not an audited production certification.
Before a live chain rollout, complete:
- secrets rotation
- TLS certificates
- SSO integration
- penetration testing
- staging and pilot validation

## Compliance and legal package
- `docs/compliance/`
- `legal/`
- `scripts/compliance/run-compliance-check.sh`
- audit middleware enabled by default


## Startup verification
This bundle was rebuilt with missing auth modules restored and validated with import + pytest smoke tests in the packaging environment.

## Runtime helpers
- `scripts/runtime/run-docker-runtime.sh`
- `scripts/runtime/validate-k8s-manifests.sh`
- `scripts/runtime/deploy-k8s-runtime.sh`
