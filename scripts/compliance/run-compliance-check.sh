#!/usr/bin/env bash
set -u
set -o pipefail

FAILURES=0

check_file() {
  if [ -f "$1" ]; then
    echo "[PASS] $1"
  else
    echo "[FAIL] Missing $1"
    FAILURES=$((FAILURES+1))
  fi
}

check_dir() {
  if [ -d "$1" ]; then
    echo "[PASS] $1"
  else
    echo "[FAIL] Missing directory $1"
    FAILURES=$((FAILURES+1))
  fi
}

echo "Running VE-JIT compliance checks..."
check_file "legal/terms_of_service.md"
check_file "legal/privacy_policy.md"
check_file "legal/data_processing_addendum.md"
check_file "docs/compliance/data_retention_policy.md"
check_file "docs/compliance/data_deletion_workflow.md"
check_file "docs/compliance/incident_response_runbook.md"
check_file "backend/app/middleware/audit.py"
check_dir "audit_logs"

if [ -f "audit_logs/audit.log" ]; then
  echo "[PASS] audit log present"
else
  echo "[WARN] audit log file not created yet; start app and hit endpoints to generate it"
fi

if [ "$FAILURES" -gt 0 ]; then
  echo "Compliance check failed: $FAILURES issue(s)"
  exit 1
fi

echo "Compliance check passed"
