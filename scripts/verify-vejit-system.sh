#!/usr/bin/env bash
set -u
set -o pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

ERRORS=0
WARNINGS=0
PASSED=0
DOCKER_AVAILABLE=true

log_test(){ echo -e "${BLUE}[TEST]${NC} $1"; }
log_pass(){ echo -e "${GREEN}[PASS]${NC} $1"; PASSED=$((PASSED+1)); }
log_fail(){ echo -e "${RED}[FAIL]${NC} $1"; ERRORS=$((ERRORS+1)); }
log_warn(){ echo -e "${YELLOW}[WARN]${NC} $1"; WARNINGS=$((WARNINGS+1)); }

log_test "Checking dependencies..."
for dep in curl python3 docker bash; do
  if command -v "$dep" >/dev/null 2>&1; then
    log_pass "Found: $dep"
  else
    log_fail "Missing dependency: $dep"
  fi
done

log_test "Checking Python SSL..."
if python3 -c "import ssl" >/dev/null 2>&1; then
  log_pass "Python SSL module available"
else
  log_fail "Python SSL module missing"
fi

log_test "Checking Docker daemon state..."
if docker info >/dev/null 2>&1; then
  log_pass "Docker daemon is running"
else
  log_fail "Docker daemon is not running (start with: sudo systemctl start docker)"
  DOCKER_AVAILABLE=false
fi

if [ "$DOCKER_AVAILABLE" = true ]; then
  log_test "Checking compose file..."
  if docker compose config >/dev/null 2>&1; then
    log_pass "docker compose config is valid"
  else
    log_fail "docker compose config failed"
  fi
else
  log_warn "Skipping Docker-dependent checks"
fi

echo
echo "Passed:   $PASSED"
echo "Warnings: $WARNINGS"
echo "Errors:   $ERRORS"

if [ "$ERRORS" -gt 0 ]; then
  exit 1
fi
