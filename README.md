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
- HTML dashboard UI served by FastAPI (includes a live Three Planes panel)
- PostgreSQL + Redis + Kafka + Prometheus + Grafana Docker stack
- Tests, CI workflow, verification and deployment scripts
- Kubernetes starter manifests

## Three planes (Commerce / Operations / Intelligence)

The platform splits responsibilities across three planes sharing one trusted
data layer — see [`docs/THREE_PLANES.md`](docs/THREE_PLANES.md) for the full
contract. Quick tour:

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
