# VE-JIT Enterprise Repository

VE-JIT is a production-oriented starter platform for retail and food-chain inventory optimization,
queue management, procurement automation, and supply-chain visibility.

## Included
- FastAPI backend with health, inventory, queue, IoT, analytics, and JIT demo endpoints
- HTML dashboard UI served by FastAPI
- PostgreSQL + Redis + Kafka + Prometheus + Grafana Docker stack
- Tests, CI workflow, verification and deployment scripts
- Kubernetes starter manifests

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
