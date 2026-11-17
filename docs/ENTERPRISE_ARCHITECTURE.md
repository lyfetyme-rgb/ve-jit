# Enterprise Architecture

UI Dashboard -> FastAPI API (grouped by plane) -> JIT / Queue / Analytics
Services -> Vendarix edge runtime -> Commerce / Operations / Intelligence
planes -> PostgreSQL / Redis / Kafka -> Monitoring

Three planes over one trusted data layer (full contract: `THREE_PLANES.md`):

- **Commerce** — customer transactions: POS integration, orders and
  transactions, pricing and promotions, loyalty, eCommerce, customer-facing
  workflows.
- **Operations** — physical business processes: inventory ledger, purchasing
  and procurement, replenishment, receiving and put-away, DC transfers,
  supplier terms and SLAs, vendor collaboration.
- **Intelligence** — analytical decisions: demand forecasting, inventory
  anomaly detection, shelf availability insights, queue and workforce
  guidance, supplier intelligence, waste and spoilage analysis.

Governing rule: Intelligence produces recommendations. Operations executes
authorized business actions. Commerce remains responsible for the
customer-facing transaction.

The **Vendarix edge node** (`app/edge/`, `docs/EDGE_ARCHITECTURE.md`) is the
operational engine around the planes: adapters for NCR Voyix, Toshiba TCGS
(JavaPOS/UPOS), Diebold Nixdorf and bi-optic scanner-scale hardware, a shared
event contract with payload-hash integrity, deterministic policy gates with
human authorization requests, a continuity state machine (ONLINE / DEGRADED /
OFFLINE / SAFE_MODE / RECOVERING) with a persisted continuity value, a
transactional outbox, and explicit verify → replay → resolve → acknowledge
recovery. Approved local AI skills live under `skills/` and execute as
isolated subprocesses with a stdin/stdout JSON contract; they never touch the
operational database directly.

Cloud scale path:
- Docker Compose for dev
- Kubernetes for staging and production
- CI/CD via GitHub Actions
