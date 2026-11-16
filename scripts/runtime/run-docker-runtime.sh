#!/usr/bin/env bash
set -u
set -o pipefail

echo "Starting VE-JIT Docker runtime..."
docker compose up -d --build

echo "Services should be available at:"
echo "- API docs: http://localhost:8000/docs"
echo "- Dashboard: http://localhost:8000/ui/"
echo "- Prometheus: http://localhost:9090"
echo "- Grafana: http://localhost:3000"
