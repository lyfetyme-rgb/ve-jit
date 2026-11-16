#!/usr/bin/env bash
set -u
set -o pipefail

if ! command -v kubectl >/dev/null 2>&1; then
  echo "kubectl is not installed"
  exit 1
fi

kubectl apply --dry-run=client -k infrastructure/kubernetes
