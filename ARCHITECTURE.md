# VE-JIT Architecture

## Core layers
- UI Dashboard (three planes overview, health, queue, JIT)
- FastAPI API Layer — grouped by plane
- JIT / Queue / Analytics Services
- **Commerce plane** — POS/eCommerce transaction rail, price book, promotions, loyalty (`app/api/commerce.py`, `app/planes/commerce.py`)
- **Operations plane** — append-only inventory ledger, procurement, receiving, transfers, supplier terms, vendor collaboration (`app/api/operations.py`, `app/planes/operations.py`)
- **Intelligence plane** — forecasting, anomaly detection, availability, workforce guidance, supplier scorecards, waste analysis; recommendations only (`app/api/intelligence.py`, `app/planes/intelligence.py`)
- PostgreSQL persistence
- Redis caching
- Kafka event streaming
- Prometheus / Grafana observability

Governing rule: Intelligence produces recommendations. Operations executes
authorized business actions. Commerce remains responsible for the
customer-facing transaction. See `docs/THREE_PLANES.md`.

## API groups
- `/health`
- `/api/v1/inventory`
- `/api/v1/queue`
- `/api/v1/iot`
- `/api/v1/analytics`
- `/api/v1/jit`
- `/api/v1/planes` — plane registry and governance rule
- `/api/v1/commerce` — POS/eCommerce orders, pricing, promotions, loyalty
- `/api/v1/operations` — ledger, adjustments, waste, procurement, receiving, transfers, vendors, action execution
- `/api/v1/intelligence` — forecasts, anomalies, shelf availability, workforce guidance, supplier scorecards, waste analysis, recommendations

Plane details and the normative spec: `docs/THREE_PLANES.md`.
