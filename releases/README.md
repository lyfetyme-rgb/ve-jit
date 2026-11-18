# Release manifests (SKU → release mapping)

Every tagged release publishes `releases/<tag>/manifest.json` alongside the
`.deb` / `.tar.gz` bundles and `SHA256SUMS.txt`. It maps each store-side
component (`VEJIT-ADAPTER`, `VEJIT-SYNC`, `VEJIT-EDGE`, `VEJIT-SKILLS`,
`VEJIT-SQL`, …) and **every file inside each platform tarball** to its SHA256
— the machine-readable provenance operators and QSAs use to verify what ships
to a store.

```bash
# Generate for the current working tree (CI/release pipeline does this automatically)
python tools/release_manifest.py --local-dir dist --version v0.3.0 --out releases/v0.3.0/manifest.json

# Re-generate from a published release into releases/<tag>/manifest.json
python tools/release_manifest.py --from-tag v0.2.0 --out releases/v0.2.0/manifest.json
```

| Field | Meaning |
|---|---|
| `schema` | `vendarix.release.manifest.v1` |
| `platforms.<arch>.debian_package` / `bundle` | name, size, SHA256 of the published artifacts |
| `platforms.<arch>.files` | every file in the tarball: path, size, SHA256, component |
| `components` | component IDs, descriptions, licenses |

The manifest is deterministic (sorted keys) and attached to the GitHub Release
automatically by `.github/workflows/release.yml` — no operator action required.
`releases/v0.2.0/manifest.json` is committed as the first generated instance.
