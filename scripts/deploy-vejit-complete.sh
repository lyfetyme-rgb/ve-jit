#!/usr/bin/env bash
set -u
set -o pipefail

echo "Starting VE-JIT enterprise stack..."
docker compose up -d --build
echo "VE-JIT started."
echo "API docs: http://localhost:8000/docs"
echo "Dashboard: http://localhost:8000/ui/"
