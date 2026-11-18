# Release Bundles

Every tagged release (`git tag v0.1.0 && git push origin v0.1.0`) runs the
**Release Edge Bundles** workflow (`.github/workflows/release.yml`) and publishes,
per architecture, a self-contained `.deb` and `.tar.gz` plus `SHA256SUMS.txt` to
GitHub Releases:

```
vendarix-edge_0.1.0_amd64.deb / .tar.gz
vendarix-edge_0.1.0_arm64.deb / .tar.gz
SHA256SUMS.txt
```

## Bundle layout (inside either artifact)

```
opt/vendarix/
  bin/vendarix-hw-adapter   # stripped C++ sidecar (hardened build: FORTIFY, stack protector, PIE, full RELRO)
  bin/vejit-sync            # static Go CLI (CGO_ENABLED=0, trimmed, version-stamped)
  edge/
    app/                    # VE-JIT FastAPI edge core (Python 3.11+)
    skills/                 # stdlib-only AI skills (approved, catalog, tests)
    infrastructure/database/append_only.sql   # trigger file applied at service start
    requirements-prod.txt
    systemd/                # copies of the unit files
    scripts/install-edge.sh # idempotent installer
etc/systemd/system/         # vendarix-edge.service, vendarix-hw-adapter.service, vendarix-edge.target
```

## Release flow

1. **verify-gates** — full test suite + skill schema/isolation audit + `go vet`/`go test -race` + C++ `-Werror` build and UDS integration test (this is the gate; a red gate blocks packaging)
2. **build-and-package** (matrix `amd64`, `arm64`) — cross-compiles the sidecars (`aarch64-linux-gnu-g++` for arm64), packages with `dpkg-deb`, generates per-arch `.sha256`
3. **publish-release** — merges checksums, **generates `manifest.json` (the SKU → release mapping)** and creates the GitHub Release with generated notes; artifacts attach automatically

## Manual build

```bash
make -C services/hardware_adapter release ARCH=amd64    # needs g++ and nlohmann-json3-dev
make -C services/sync-go release GOARCH=amd64 VERSION=v0.1.0
./scripts/package_edge.sh v0.1.0 amd64                  # needs rsync, dpkg-dev, sha256sum
```

## Store rollout (per machine)

```bash
# tarball: sudo tar -C / -xzf vendarix-edge_0.1.0_amd64.tar.gz
# .deb:    sudo dpkg -i vendarix-edge_0.1.0_amd64.deb
sudo /opt/vendarix/edge/scripts/install-edge.sh
journalctl -u vendarix-edge -f
```

The installer is idempotent: it creates the `vendarix-edge`/`vendarix-hw` users,
`/var/lib/vendarix`, `/run/vendarix`, `/opt/vendarix/venv`, installs the
runtime dependencies, applies the append-only triggers to the local SQLite
database, installs + enables the systemd units, starts `vendarix-edge.target`
and waits for `/run/vendarix/edge.sock` to answer `/health`. Fresh databases should run `infrastructure/migrations/migrate.py` after the app's first start; see that directory's README.

Architecture assignment: `amd64` → store back-office servers and POS lanes;
`arm64` → compact edge gateways. Both bundles are identical except for the
compiled binaries; Python and the skills are pure `py3` + stdlib.

Deviation note: the bundle layout here is `opt/`/`etc/` (dpkg-compat layout),
not the flatter `bin/ app/ systemd/ scripts/` sketch in the original proposal —
it is required for `dpkg-deb --build` and for `systemd` to find the units
unambiguously. The repo also lacks a root `pyproject.toml` (it's a
requirements-driven app); packaging installs `backend/requirements-prod.txt`
into `/opt/vendarix/venv` instead of `pip install -e .`.
