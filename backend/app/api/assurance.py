"""BENGAL BAA assurance API — observation only, redacted output only.

No route here can approve, deny or execute a governed action. Reports are
count/score/fingerprint artifacts safe for the audit ledger and public health.
"""

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.assurance.bengal import BengalCase, BengalEngine, ThreatMode
from app.assurance.bengal_eval import PROTOCOL_VERSION, EvalReport, load_cases, run_eval
from app.config import settings
from app.pci_guard import forbid_cardholder_data_body

router = APIRouter(prefix="/api/v1/assurance/bengal", tags=["assurance"])

MODES = tuple(m.value for m in ThreatMode)
METHOD_VERSION = "bengal-baa/v1"
CORPUS_PATH = "../fixtures/bengal/cases.jsonl"  # relative to backend/ cwd


class AnalyzeRequest(BaseModel):
    case: BengalCase
    modes: list[ThreatMode] = Field(default_factory=lambda: list(ThreatMode))


class EvalRequest(BaseModel):
    model_id: str = ""
    min_f1: float = Field(0.0, ge=0.0, le=1.0)


class AnalyzeResponse(BaseModel):
    summary: dict[str, Any]
    observation_only: bool = True


@router.get("/status")
def status():
    """Static capability metadata for the public health surface — no case data."""
    return {
        "enabled": True,
        "method_version": METHOD_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "modes": list(MODES),
        "observation_only": True,
        "note": "BENGAL analyses never approve, deny or execute governed actions.",
    }


@router.post("/analyze", response_model=AnalyzeResponse)
async def analyze(
    payload: AnalyzeRequest,
    _pci: None = Depends(forbid_cardholder_data_body("assurance.bengal.analyze")),
):
    engine = BengalEngine(secret=settings.SECRET_KEY)
    report = await engine.analyze(payload.case, modes=payload.modes)
    return AnalyzeResponse(summary=report.summary())


@router.post("/eval", response_model=EvalReport)
async def evaluate(payload: EvalRequest, _pci: None = Depends(forbid_cardholder_data_body("assurance.bengal.eval"))):
    from pathlib import Path

    path = Path(CORPUS_PATH)
    if not path.exists():
        path = Path(__file__).resolve().parents[2] / "fixtures" / "bengal" / "cases.jsonl"
    cases = load_cases(path)
    engine = BengalEngine(secret=settings.SECRET_KEY)
    report = await run_eval(cases, engine, model_id=payload.model_id)
    if not report.verified:
        raise HTTPException(status_code=500, detail="BENGAL eval failed internal reconciliation check")
    if report.macro_f1 < payload.min_f1:
        raise HTTPException(status_code=422, detail=f"macro F1 {report.macro_f1} below requested threshold {payload.min_f1}")
    return report


# Guard is attached per-route via Depends above. No route in this module writes
# to the operational or audit tables, and no result can authorize an action.
