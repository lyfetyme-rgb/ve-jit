# Enterprise Architecture

UI Dashboard -> FastAPI API (grouped by plane) -> JIT / Queue / Analytics
Services -> Commerce / Operations / Intelligence planes -> PostgreSQL / Redis /
Kafka -> Monitoring

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

Cloud scale path:
- Docker Compose for dev
- Kubernetes for staging and production
- CI/CD via GitHub Actions
