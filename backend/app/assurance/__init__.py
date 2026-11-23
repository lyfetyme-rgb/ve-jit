"""Independent assurance (BENGAL BAA) for VE-JIT.

Adapted from the IARPA BENGAL BAA-24-03 analyzers. Observation only: assessments
never approve, deny, authorize or execute. Reports contain case ids, 16-hex
fingerprints, counts and scores only — no raw payloads.

Four deterministic, model-agnostic threat-mode analyzers:

1. bias / diverse perspectives
2. hallucination / plausible inference
3. safe information flow (verifiable redaction, aggregation, access likelihood)
4. poisoned / imperfect sources (provenance, corroboration, intent)
"""

from app.assurance.bengal import (
    BengalCase,
    BengalEngine,
    BengalReport,
    BengalThresholds,
    ClaimStatus,
    Severity,
    SourceDocument,
    SourceIntent,
    ThreatMode,
    Verdict,
)

__all__ = [
    "ThreatMode",
    "Severity",
    "ClaimStatus",
    "SourceIntent",
    "Verdict",
    "BengalCase",
    "BengalEngine",
    "BengalReport",
    "BengalThresholds",
    "SourceDocument",
]
