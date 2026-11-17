#!/usr/bin/env bash
# Package the Vendarix edge bundle: .tar.gz + .deb + SHA256SUMS.
#
# Usage: scripts/package_edge.sh <version> <arch>
#   <version>  e.g. v0.1.0 (the v prefix is stripped for Debian metadata)
#   <arch>     amd64 | arm64
#
# Requires: rsync, dpkg-deb, sha256sum, python3 (for the skills guard),
# and the prebuilt sidecar binaries at:
#   services/hardware_adapter/build/vendarix-hw-adapter
#   services/sync-go/build/vejit-sync
set -euo pipefail

VERSION="${1:?Usage: $0 <version> <arch: amd64|arm64>}"
ARCH="${2:?Usage: $0 <version> <arch: amd64|arm64>}"
CLEAN_VERSION="${VERSION#v}"

DIST_DIR="dist"
PKG_NAME="vendarix-edge_${CLEAN_VERSION}_${ARCH}"
STAGE_DIR="${DIST_DIR}/${PKG_NAME}"

case "${ARCH}" in
  amd64|arm64) ;;
  *) echo "unsupported arch ${ARCH}; expected amd64 or arm64" >&2; exit 2 ;;
esac

rm -rf "${STAGE_DIR}"
mkdir -p "${STAGE_DIR}/opt/vendarix/bin"
mkdir -p "${STAGE_DIR}/opt/vendarix/edge"
mkdir -p "${STAGE_DIR}/etc/systemd/system"
mkdir -p "${STAGE_DIR}/DEBIAN"

# 1. Re-verify skill stdlib-only isolation before packaging
python3 tools/validate_skills.py

# 2. Copy compiled sidecar binaries
install -m 0755 "services/hardware_adapter/build/vendarix-hw-adapter" "${STAGE_DIR}/opt/vendarix/bin/vendarix-hw-adapter"
install -m 0755 "services/sync-go/build/vejit-sync" "${STAGE_DIR}/opt/vendarix/bin/vejit-sync"

# 3. Copy the Python edge core & the isolated stdlib-only skills
#    (path layout inside the bundle mirrors the repo: edge/ serves as WorkingDirectory)
#    Only infrastructure/database is carried (needed by ExecStartPre), not the k8s manifests.
mkdir -p "${STAGE_DIR}/opt/vendarix/edge/infrastructure"
rsync -a \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='.pytest_cache' \
  --exclude='.venv*' \
  --exclude='tests' \
  backend/app backend/requirements-prod.txt skills \
  "${STAGE_DIR}/opt/vendarix/edge/"
rsync -a \
  --exclude='__pycache__' \
  infrastructure/database \
  "${STAGE_DIR}/opt/vendarix/edge/infrastructure/"

# 4. Copy hardened systemd units
install -m 0644 services/hardware_adapter/vendarix-hw-adapter.service "${STAGE_DIR}/etc/systemd/system/"
install -m 0644 infrastructure/systemd/vendarix-edge.service "${STAGE_DIR}/etc/systemd/system/"
install -m 0644 infrastructure/systemd/vendarix-edge.target "${STAGE_DIR}/etc/systemd/system/"

# 4b. Ship the idempotent installer and the unit files inside the bundle
mkdir -p "${STAGE_DIR}/opt/vendarix/edge/scripts"
install -m 0755 scripts/install-edge.sh "${STAGE_DIR}/opt/vendarix/edge/scripts/install-edge.sh"
mkdir -p "${STAGE_DIR}/opt/vendarix/edge/systemd"
install -m 0644 services/hardware_adapter/vendarix-hw-adapter.service "${STAGE_DIR}/opt/vendarix/edge/systemd/"
install -m 0644 infrastructure/systemd/vendarix-edge.service "${STAGE_DIR}/opt/vendarix/edge/systemd/"
install -m 0644 infrastructure/systemd/vendarix-edge.target "${STAGE_DIR}/opt/vendarix/edge/systemd/"

# 5. Debian control + postinst for zero-touch store rollout
cat > "${STAGE_DIR}/DEBIAN/control" <<EOF
Package: vendarix-edge
Version: ${CLEAN_VERSION}
Section: admin
Priority: optional
Architecture: ${ARCH}
Depends: python3 (>= 3.11), python3-venv, sqlite3
Maintainer: Vendarix Platform Engineering <platform@vendarix.io>
Description: Vendarix VE-JIT Edge Runtime, Hardware Adapter, and Sync Sidecar
EOF

cat > "${STAGE_DIR}/DEBIAN/postinst" <<'EOF'
#!/usr/bin/env bash
set -e
id -u vendarix-edge >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin vendarix-edge
id -u vendarix-hw >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin -g vendarix-edge vendarix-hw

mkdir -p /var/lib/vendarix /var/log/vendarix-hw /run/vendarix
chown -R vendarix-edge:vendarix-edge /var/lib/vendarix /opt/vendarix/edge
chown vendarix-hw:vendarix-edge /var/log/vendarix-hw
chmod 0750 /run/vendarix

systemctl daemon-reload
echo "vendarix-edge installed. Run scripts/install-edge.sh (or /opt/vendarix/edge/scripts/install-edge.sh from the tarball) to finish setup."
EOF
chmod 0755 "${STAGE_DIR}/DEBIAN/postinst"

# 6. Build .deb and portable .tar.gz (tarball carries opt/ + etc/, mirroring the .deb payload)
dpkg-deb --build --root-owner-group "${STAGE_DIR}" "${DIST_DIR}/${PKG_NAME}.deb"
tar -czf "${DIST_DIR}/${PKG_NAME}.tar.gz" -C "${DIST_DIR}" "${PKG_NAME}/opt" "${PKG_NAME}/etc"

# 7. Generate SHA-256 checksums
(cd "${DIST_DIR}" && sha256sum "${PKG_NAME}.deb" "${PKG_NAME}.tar.gz" > "${PKG_NAME}.sha256")
echo "Packaged ${PKG_NAME}:"
(cd "${DIST_DIR}" && sha256sum "${PKG_NAME}.deb" "${PKG_NAME}.tar.gz")
