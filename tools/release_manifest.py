#!/usr/bin/env python3
"""Release manifest / software bill of materials generator.

Every tagged release ships with a machine-readable manifest mapping each
component (store-side) and every file in the bundle to its digest — the
"SKU -> release" mapping: v0.2.0 -> vendarix-edge_0.2.0_{amd64,arm64}.{deb,tar.gz}
-> per-file SHA256.

Two modes:

  --from-tag v0.2.0
      Downloads the published release assets from GitHub, hashes the .deb and
      .tar.gz files, enumerates every file inside each tarball, and writes
      releases/<tag>/manifest.json

  --local-dir dist/vX.Y.Z
      Same, but against an existing dist/ directory produced by
      scripts/package_edge.sh (used by the release workflow before publishing)

The output is deterministic JSON: sort keys, fixed timestamps passed in via
--now or the commit time of the tag (fallback: current UTC time).

Examples:
  python tools/release_manifest.py --from-tag v0.2.0 --out releases/v0.2.0/manifest.json
  python tools/release_manifest.py --local-dir dist --version v0.2.0 --out dist/manifest.json
"""

import argparse
import hashlib
import io
import json
import re
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

REPO = "lyfetyme-rgb/ve-jit"
API = f"https://api.github.com/repos/{REPO}"
ROOT = Path(__file__).resolve().parent.parent

# An artifact or file's forged identity goes in here; the field is descriptive only.
COMPONENT_MAP = (
    ("bin/vendarix-hw-adapter", "VEJIT-ADAPTER", "C++, static/stripped, FORTIFY+PIE", "Business Source License 1.1"),
    ("bin/vejit-sync", "VEJIT-SYNC", "Go, static (CGO_ENABLED=0)", "Business Source License 1.1"),
    ("opt/vendarix/edge/app/", "VEJIT-EDGE", "Python FastAPI edge runtime", "Business Source License 1.1"),
    ("opt/vendarix/edge/skills/", "VEJIT-SKILLS", "6 stdlib-only AI skills (subprocess JSON)", "Business Source License 1.1"),
    ("opt/vendarix/edge/infrastructure/database/", "VEJIT-SQL", "append-only trigger set + schema notes", "Business Source License 1.1"),
    ("etc/systemd/system/vendarix-edge.service", "VEJIT-SD-EDGE", "systemd unit", "Business Source License 1.1"),
    ("etc/systemd/system/vendarix-hw-adapter.service", "VEJIT-SD-HW", "systemd unit", "Business Source License 1.1"),
    ("etc/systemd/system/vendarix-edge.target", "VEJIT-SD-TGT", "systemd target", "Business Source License 1.1"),
)


def http_json(url: str):
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                               "User-Agent": "ve-jit-release-manifest"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


def http_bytes(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"Accept": "application/octet-stream",
                                               "User-Agent": "ve-jit-release-manifest"})
    with urllib.request.urlopen(req, timeout=300) as resp:
        return resp.read()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def enumerate_tarball(data: bytes) -> list[dict]:
    files = []
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for member in tar:
            if not member.isfile():
                continue
            content = tar.extractfile(member).read()
            files.append({
                "path": member.name,
                "size": len(content),
                "sha256": sha256(content),
                "component": classify(member.name),
            })
    files.sort(key=lambda f: f["path"])
    return files


def classify(path: str) -> str:
    norm = path if "/" in path else path
    # Strip any leading top-level bundle dir (e.g. vendarix-edge_0.2.0_amd64/).
    m = re.match(r"^[^/]*(?:/)?(.*)$", path)
    rel = m.group(1) if m else path
    for prefix, comp, _desc, _lic in COMPONENT_MAP:
        if rel == prefix or rel.startswith(prefix) or rel.endswith(prefix):
            return comp
    return "VEJIT-MISC"


def artifact_record(name: str, data: bytes) -> dict:
    return {"name": name, "size": len(data), "sha256": sha256(data)}


def build_manifest(version: str, tag: str, commit: str, produced_at: str,
                   arch_assets: dict[str, dict[str, bytes]]) -> dict:
    platforms = {}
    for arch, assets in arch_assets.items():
        tarballs = [a for n, a in assets.items() if n.endswith(".tar.gz")]
        debs = [a for n, a in assets.items() if n.endswith(".deb")]
        tarball = [artifact_record(n, a) for n, a in assets.items() if n.endswith(".tar.gz")]
        deb = [artifact_record(n, a) for n, a in assets.items() if n.endswith(".deb")]
        files = [enumerate_tarball(a) for a in tarballs]
        files = files[0] if files else []
        platforms[arch] = {
            "debian_package": deb,
            "bundle": tarball,
            "files": files,
            "file_count": len(files),
        }
    return {
        "schema": "vendarix.release.manifest.v1",
        "product": "vendarix-edge",
        "version": version.lstrip("v"),
        "tag": tag,
        "commit": commit,
        "produced_at": produced_at,
        "platforms": platforms,
        "components": [
            {"id": cid, "description": desc, "license": lic}
            for _prefix, cid, desc, lic in COMPONENT_MAP
        ],
    }


def assets_for_release(tag: str) -> dict[str, dict[str, bytes]]:
    release = http_json(f"{API}/releases/tags/{tag}")
    by_arch: dict[str, dict[str, bytes]] = {}
    for asset in release.get("assets", []):
        name = asset["name"]
        if not (name.endswith(".tar.gz") or name.endswith(".deb")):
            continue
        m = re.search(r"_(amd64|arm64)\.", name)
        if not m:
            continue
        arch = m.group(1)
        by_arch.setdefault(arch, {})[name] = http_bytes(asset["browser_download_url"])
    return by_arch


def assets_for_local(local_dir: Path, version: str) -> dict[str, dict[str, bytes]]:
    cleann = version.lstrip("v")
    by_arch: dict[str, dict[str, bytes]] = {}
    for arch in ("amd64", "arm64"):
        deb = local_dir / f"vendarix-edge_{cleann}_{arch}.deb"
        tar = local_dir / f"vendarix-edge_{cleann}_{arch}.tar.gz"
        assets = {}
        if deb.exists():
            assets[deb.name] = deb.read_bytes()
        if tar.exists():
            assets[tar.name] = tar.read_bytes()
        if assets:
            by_arch[arch] = assets
    return by_arch


def main() -> int:
    ap = argparse.ArgumentParser()
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--from-tag", help="download published release assets for this tag")
    src.add_argument("--local-dir", type=Path, help="directory containing freshly built .deb/.tar.gz")
    ap.add_argument("--version", help="required with --local-dir, e.g. v0.2.0")
    ap.add_argument("--out", required=True, type=Path, help="where to write manifest.json")
    ap.add_argument("--commit", default="", help="commit sha (filled automatically for --from-tag)")
    ap.add_argument("--produced-at", default="", help="override produced timestamp (ISO 8601)")
    args = ap.parse_args()

    if args.from_tag:
        tag = args.from_tag
        version = tag
        release = http_json(f"{API}/releases/tags/{tag}")
        commit = release.get("target_commitish", "")
        produced_at = release.get("published_at", "")
        arch_assets = assets_for_release(tag)
        if not arch_assets:
            print(f"no artifacts found for {tag}", file=sys.stderr)
            return 1
    else:
        if not args.version:
            print("--version is required with --local-dir", file=sys.stderr)
            return 1
        tag = args.version
        version = args.version
        commit = args.commit
        produced_at = args.produced_at
        arch_assets = assets_for_local(args.local_dir, args.version)
        if not arch_assets:
            print("no artifacts found in directory", file=sys.stderr)
            return 1

    if not produced_at:
        from datetime import datetime, timezone

        produced_at = datetime.now(timezone.utc).isoformat()

    manifest = build_manifest(version, tag, commit, produced_at, arch_assets)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {args.out} ({sum(p['file_count'] for p in manifest['platforms'].values())} files across {len(manifest['platforms'])} platform(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())
