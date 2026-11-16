#!/usr/bin/env bash
set -u
set -o pipefail

mkdir -p legal docs/compliance audit_logs
touch audit_logs/.gitkeep

echo "VE-JIT legal and compliance package initialized."
echo "Review and customize all legal templates before customer use."
