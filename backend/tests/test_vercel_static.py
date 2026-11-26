"""Vercel static-dashboard wiring checks (no network, no Vercel CLI needed)."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"


def test_vercel_project_targets_frontend_only():
    config = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))
    assert config["outputDirectory"] == "frontend"
    assert config["framework"] is None
    assert config["cleanUrls"] is True


def test_config_loaded_before_app():
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    assert html.index("config.js") < html.index("app.js")


def test_api_base_is_injected_without_touching_call_sites():
    config = (FRONTEND / "config.js").read_text(encoding="utf-8")
    assert "window.VEJIT_API_BASE" in config
    app = (FRONTEND / "app.js").read_text(encoding="utf-8")
    assert "function apiUrl(path)" in app
    assert "fetch(apiUrl(url), options)" in app
    assert 'window.VEJIT_API_BASE = ""' not in app  # only config.js owns the default
    # Every relative API path still flows through fetchJson -> apiUrl.
    assert app.count('fetchJson("/api/v1/') >= 10


def test_no_secrets_in_static_bundle():
    for path in FRONTEND.glob("*"):
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="replace").lower()
            for forbidden in ("api_token=", "cloud_signer_token", "policy_signing_key=", "password="):
                assert forbidden not in text, f"{path.name} must not contain secrets"
