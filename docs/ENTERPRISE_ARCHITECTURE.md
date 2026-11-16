# Enterprise Architecture

UI Dashboard -> FastAPI API -> JIT / Queue / Analytics Services -> PostgreSQL / Redis / Kafka -> Monitoring

Cloud scale path:
- Docker Compose for dev
- Kubernetes for staging and production
- CI/CD via GitHub Actions
