#!/usr/bin/env bash
# Zero-touch Vendarix edge rollout (idempotent — safe to re-run).
#
# Works from either:
#   - an extracted release tarball:  cd vendarix-edge_<version>_<arch> && sudo ./opt/vendarix/edge/scripts/install-edge.sh
#   - an installed .deb payload:     sudo /opt/vendarix/edge/scripts/install-edge.sh
#   - the source repo:               sudo scripts/install-edge.sh
#
# What it does (every step re-applies cleanly):
#   1. sanity-checks python3 >= 3.11, sqlite3, systemd
#   2. creates the vendarix-edge / vendarix-hw system users (if absent)
#   3. creates /opt/vendarix, /var/lib/vendarix, /var/log/vendarix-hw, /run/vendarix
#   4. builds /opt/vendarix/venv and installs backend/requirements-prod.txt
#   5. applies the SQLite append-only triggers to /var/lib/vendarix/edge.db
#   6. installs + enables the systemd units and starts the edge target
#   7. waits for /run/vendarix/edge.sock and checks /health over the UDS
set -euo pipefail

require_root() {
  if [ "$(id -u)" -ne 0 ]; then
    echo "error: run as root (sudo $0)" >&2
    exit 1
  fi
}

log() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }

require_root
log "Checking prerequisites"
python3 -c 'import sys; assert sys.version_info >= (3, 11), "python3 >= 3.11 required"'
for tool in sqlite3 systemctl useradd id chown chmod mkdir; do
  command -v "$tool" >/dev/null || { echo "error: missing required tool: $tool" >&2; exit 1; }
done

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -d "${SRC_DIR}/../app" ]; then
  # Running from the installed payload: .../edge/scripts/install-edge.sh
  EDGE_DIR="$(cd "${SRC_DIR}/.." && pwd)"
else
  REPO_ROOT="$(cd "${SRC_DIR}/.." && pwd)"
  EDGE_DIR="/opt/vendarix/edge"       # repo checkout: stage content under /opt
  log "Installing edge payload from ${REPO_ROOT} into ${EDGE_DIR}"
  mkdir -p "${EDGE_DIR}"
  tar -C "${REPO_ROOT}/backend" -cf - app requirements-prod.txt | tar -C "${EDGE_DIR}" -xf -
  tar -C "${REPO_ROOT}" -cf - skills | tar -C "${EDGE_DIR}" -xf -
  mkdir -p "${EDGE_DIR}/infrastructure"
  tar -C "${REPO_ROOT}/infrastructure" -cf - database systemd | tar -C "${EDGE_DIR}/infrastructure" -xf -
  if [ -d "${EDGE_DIR}/infrastructure/systemd" ]; then
    rm -rf "${EDGE_DIR}/systemd"
    cp -a "${EDGE_DIR}/infrastructure/systemd" "${EDGE_DIR}/systemd"
  fi
fi

log "Creating users"
if ! id -u vendarix-edge >/dev/null 2>&1; then
  useradd --system --no-create-home --shell /usr/sbin/nologin vendarix-edge
fi
if ! id -u vendarix-hw >/dev/null 2>&1; then
  useradd --system --no-create-home --shell /usr/sbin/nologin -g vendarix-edge vendarix-hw
fi

log "Creating directories and permissions"
mkdir -p /opt/vendarix/venv /var/lib/vendarix /var/log/vendarix-hw /run/vendarix
chown -R vendarix-edge:vendarix-edge /var/lib/vendarix "${EDGE_DIR}"
chown vendarix-hw:vendarix-edge /var/log/vendarix-hw
chmod 0750 /run/vendarix

log "Building /opt/vendarix/venv and installing runtime dependencies"
if [ ! -x /opt/vendarix/venv/bin/python ]; then
  python3 -m venv /opt/vendarix/venv
fi
/opt/vendarix/venv/bin/pip install --upgrade pip >/dev/null
/opt/vendarix/venv/bin/pip install -r "${EDGE_DIR}/requirements-prod.txt" >/dev/null

log "Applying append-only database triggers to /var/lib/vendarix/edge.db"
touch /var/lib/vendarix/edge.db
sqlite3 /var/lib/vendarix/edge.db ".read ${EDGE_DIR}/infrastructure/database/append_only.sql"

log "Installing systemd units"
shopt -s nullglob
cp -f "${EDGE_DIR}"/systemd/*.service /etc/systemd/system/
if compgen -G "${EDGE_DIR}"/systemd/*.target >/dev/null; then
  cp -f "${EDGE_DIR}"/systemd/*.target /etc/systemd/system/
fi
log "Installed units from ${EDGE_DIR}/systemd"

log "Reloading systemd and enabling services"
systemctl daemon-reload
systemctl enable --now vendarix-edge.target
systemctl restart vendarix-edge.target

log "Waiting for the edge socket (up to 60s)"
for i in $(seq 1 60); do
  if [ -S /run/vendarix/edge.sock ]; then
    if curl -fsS --unix-socket /run/vendarix/edge.sock http://localhost/health >/dev/null 2>&1; then
      log "Edge is healthy: /health OK over UDS"
      log "Done. Configure /etc/vendarix/edge.env and journalctl -u vendarix-edge -f for logs."
      exit 0
    fi
  fi
  sleep 1
done

echo "error: edge did not become healthy within 60s; inspect with: journalctl -u vendarix-edge -n 200 --no-pager" >&2
exit 1
