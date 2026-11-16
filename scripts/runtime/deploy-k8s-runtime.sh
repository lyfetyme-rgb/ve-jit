#!/usr/bin/env bash
set -u
set -o pipefail

if ! command -v kubectl >/dev/null 2>&1; then
  echo "kubectl is not installed"
  exit 1
fi

kubectl apply -k infrastructure/kubernetes
kubectl get pods -n vejit-platform
