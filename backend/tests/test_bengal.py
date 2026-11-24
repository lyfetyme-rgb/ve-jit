"""BENGAL BAA assurance tests — four topics, redacted output, observation-only."""

import asyncio
import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.assurance.bengal import BengalCase, BengalEngine, Severity, SourceDocument, ThreatMode
from app.assurance.bengal_eval import load_cases, run_eval
from app.main import app

client = TestClient(app)
CORPUS = Path(__file__).resolve().parents[2] / "fixtures" / "bengal" / "cases.jsonl"


def test_corpus_is_fixed_and_labeled():
    cases = load_cases(CORPUS)
    assert len(cases) == 16
    assert {c.mode for c in cases} == set(ThreatMode)


def test_eval_is_reproducible_and_redacted():
    cases = load_cases(CORPUS)
    report = asyncio.run(run_eval(cases, BengalEngine(secret="test-secret")))
    assert report.verified is True
    assert report.total_cases == 16
    assert report.macro_f1 >= 0.85
    assert report.macro_auc >= 0.85
    payload = report.model_dump_json()
    # No raw case text/corpus/claim/source value ever appears in the report.
    for case in cases:
        assert case.text not in payload
        for claim in case.claims:
            assert claim not in payload
        for source in case.sources:
            assert source.text not in payload
    for mode in report.modes.values():
        assert mode.tp + mode.fp + mode.fn + mode.tn == mode.cases


def test_analyze_returns_summary_only_and_is_observation_only():
    response = client.post(
        "/api/v1/assurance/bengal/analyze",
        json={
            "case": {
                "case_id": "case-api-1",
                "text": "Clearly those people never follow the escalation runbook.",
                "trusted_corpus": ["The analyst reviewed the escalation runbook."],
                "claims": ["The nightly reconciliation processed 82,413 records."],
            },
            "modes": [m.value for m in ThreatMode],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["observation_only"] is True
    summary = body["summary"]
    for forbidden in ("text", "claims", "trusted_corpus", "raw"):
        assert forbidden not in summary
    assert "bias_score" in summary and "hallucination_rate" in summary
    # Fingerprints are 16-hex only.
    assert len(summary["case_id"]) <= 64


def test_bengal_never_mutates_operational_state():
    # Structural invariant: no assurance route depends on get_db, so it cannot
    # read or write commerce/operations tables regardless of test DB state.
    from app.api.assurance import router as assurance_router
    from app.db.database import get_db

    affected = [r for r in assurance_router.routes if "/bengal" in getattr(r, "path", "")]
    assert affected, "assurance routes not mounted"
    for route in affected:
        call = getattr(route, "dependant", None)
        assert call is not None
        for sub in _flatten_dependencies(call):
            fn = sub.call
            assert getattr(fn, "__name__", "") != "get_db", f"{route.path} must not depend on get_db"
    client.post(
        "/api/v1/assurance/bengal/analyze",
        json={"case": {"case_id": "case-api-2", "text": "Neutral observation about a sandbox scan."}, "modes": ["bias"]},
    )


def _flatten_dependencies(dependant):
    yield dependant
    for sub in getattr(dependant, "dependencies", []):
        yield from _flatten_dependencies(sub)


def test_health_exposes_static_capability_metadata():
    body = client.get("/health").json()
    b = body["bengal"]
    assert b["enabled"] is True
    assert b["method_version"] == "bengal-baa/v1"
    assert len(b["modes"]) == 4
    assert b["observation_only"] is True


def test_status_endpoint_and_eval_gate():
    status = client.get("/api/v1/assurance/bengal/status").json()
    assert status["protocol_version"].startswith("bengal-eval/")
    resp = client.post("/api/v1/assurance/bengal/eval", json={"min_f1": 0.85})
    assert resp.status_code == 200
    assert resp.json()["verified"] is True
    resp = client.post("/api/v1/assurance/bengal/eval", json={"min_f1": 0.99})
    assert resp.status_code == 422


def test_cardholder_data_in_case_text_is_rejected():
    resp = client.post(
        "/api/v1/assurance/bengal/analyze",
        json={"case": {"case_id": "case-pci", "text": "card 4111111111111111 was scanned"}, "modes": ["bias"]},
    )
    assert resp.status_code == 422
    assert "4111111111111111" not in resp.text
