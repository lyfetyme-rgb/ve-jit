"""T&E protocol for the VE-JIT BENGAL BAA analyzer (adapted from USOC agents/bengal_eval.py).

Closed, replicable protocol: fixed labeled corpus, fixed analyzer, per-mode confusion
counts (TP/FP/FN/TN), precision/recall/F1, tie-aware rank ROC/AUC, Wilson 95% bounds,
macro-averaged. Results carry case ids, labels, counts and scores only — no raw text.
"""

import json
import math
from pathlib import Path
from typing import Any, Iterable, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.assurance.bengal import (
    BengalCase,
    BengalEngine,
    BengalReport,
    Severity,
    SourceDocument,
    ThreatMode,
    Verdict,
)

PROTOCOL_VERSION = "bengal-eval/v1"

POSITIVE_LABEL: dict[ThreatMode, str] = {
    ThreatMode.BIAS: "biased",
    ThreatMode.HALLUCINATION: "spurious",
    ThreatMode.SAFE_INFO_FLOW: "unsafe",
    ThreatMode.POISONED_SOURCE: "unreliable",
}
NEGATIVE_LABEL: dict[ThreatMode, str] = {
    ThreatMode.BIAS: "neutral",
    ThreatMode.HALLUCINATION: "clean",
    ThreatMode.SAFE_INFO_FLOW: "safe",
    ThreatMode.POISONED_SOURCE: "reliable",
}


# -- statistical primitives --------------------------------------------------
def wilson_interval(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    if isinstance(successes, bool) or isinstance(total, bool):
        raise ValueError("successes and total must be integers")
    successes, total = int(successes), int(total)
    if total < 0 or not 0 <= successes <= total:
        raise ValueError("invalid counts")
    if z <= 0 or not math.isfinite(z):
        raise ValueError("z must be positive finite")
    if total == 0:
        return (0.0, 0.0)
    rate = successes / total
    z2 = z * z
    denominator = 1.0 + z2 / total
    centre = (rate + z2 / (2.0 * total)) / denominator
    half = (z / denominator) * math.sqrt(rate * (1.0 - rate) / total + z2 / (4.0 * total * total))
    return (max(0.0, centre - half), min(1.0, centre + half))


def roc_auc(labels: Sequence[int], scores: Sequence[float]) -> float:
    if len(labels) != len(scores):
        raise ValueError("labels/scores length mismatch")
    if any(label not in (0, 1) for label in labels):
        raise ValueError("labels must be 0 or 1")
    if any(not math.isfinite(float(s)) for s in scores):
        raise ValueError("scores must be finite")
    positives = [float(s) for label, s in zip(labels, scores) if label == 1]
    negatives = [float(s) for label, s in zip(labels, scores) if label == 0]
    if not positives or not negatives:
        return 0.5
    order = sorted(range(len(scores)), key=lambda i: float(scores[i]))
    ranks = [0.0] * len(scores)
    start = 0
    while start < len(order):
        end = start + 1
        while end < len(order) and float(scores[order[end]]) == float(scores[order[start]]):
            end += 1
        average_rank = (start + 1 + end) / 2.0
        for position in range(start, end):
            ranks[order[position]] = average_rank
        start = end
    positive_rank_sum = sum(rank for label, rank in zip(labels, ranks) if label == 1)
    n_pos, n_neg = len(positives), len(negatives)
    auc = (positive_rank_sum - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg)
    return round(min(1.0, max(0.0, auc)), 6)


# -- prediction mapping ------------------------------------------------------
def predict(report: BengalReport, mode: ThreatMode) -> tuple[str, float]:
    if mode is ThreatMode.BIAS:
        assessment = report.bias
        if assessment is None:
            raise ValueError("BIAS report has no bias assessment")
        positive = assessment.severity in (Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL)
        return (POSITIVE_LABEL[mode] if positive else NEGATIVE_LABEL[mode], assessment.bias_score)
    if mode is ThreatMode.HALLUCINATION:
        assessment = report.hallucination
        if assessment is None:
            raise ValueError("HALLUCINATION report has no hallucination assessment")
        positive = assessment.hallucination_rate >= 0.34
        return (POSITIVE_LABEL[mode] if positive else NEGATIVE_LABEL[mode], assessment.hallucination_rate)
    if mode is ThreatMode.SAFE_INFO_FLOW:
        assessment = report.safe_flow
        if assessment is None:
            raise ValueError("SAFE_INFO_FLOW report has no safe_flow assessment")
        severe = assessment.severity in (Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL)
        verdict = assessment.access.verdict
        positive = severe or verdict is not Verdict.ALLOW
        score = (
            0.9
            if assessment.severity in (Severity.HIGH, Severity.CRITICAL) or verdict is Verdict.BLOCK
            else 0.6
            if assessment.severity is Severity.MEDIUM or verdict is Verdict.REVIEW
            else 0.0
        )
        return (POSITIVE_LABEL[mode] if positive else NEGATIVE_LABEL[mode], score)
    assessment = report.sources
    if assessment is None:
        raise ValueError("POISONED_SOURCE report has no sources assessment")
    unreliable = assessment.severity in (Severity.HIGH, Severity.CRITICAL) or any(
        c.verdict == "conflicted" for c in assessment.corroborations
    )
    return (POSITIVE_LABEL[mode] if unreliable else NEGATIVE_LABEL[mode], 0.9 if unreliable else 0.1)


# -- strict records ----------------------------------------------------------
class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EvalCase(Strict):
    case_id: str = Field(min_length=1, max_length=64)
    mode: ThreatMode
    expected_label: str
    text: str = Field(min_length=1)
    claims: tuple[str, ...] = ()
    trusted_corpus: tuple[str, ...] = ()
    sources: tuple[SourceDocument, ...] = ()
    records: tuple[dict[str, Any], ...] = ()
    perspectives: tuple[str, ...] = ()
    query: str = ""
    response: str = ""

    @field_validator("expected_label")
    @classmethod
    def _known_label(cls, value: str, info: Any) -> str:
        mode = info.data.get("mode")
        if mode is not None and value not in (POSITIVE_LABEL[mode], NEGATIVE_LABEL[mode]):
            raise ValueError(f"expected_label {value!r} is not valid for mode {mode.value}")
        return value


class CaseResult(Strict):
    case_id: str
    mode: ThreatMode
    expected: str
    predicted: str
    correct: bool
    score: float = Field(ge=0.0, le=1.0)


class ModeMetrics(Strict):
    mode: ThreatMode
    tp: int = Field(ge=0)
    fp: int = Field(ge=0)
    fn: int = Field(ge=0)
    tn: int = Field(ge=0)
    precision: float = Field(ge=0.0, le=1.0)
    recall: float = Field(ge=0.0, le=1.0)
    f1: float = Field(ge=0.0, le=1.0)
    accuracy: float = Field(ge=0.0, le=1.0)
    auc: float = Field(ge=0.0, le=1.0)
    cases: int = Field(ge=0)
    ci_low: float = Field(ge=0.0, le=1.0)
    ci_high: float = Field(ge=0.0, le=1.0)


class EvalReport(Strict):
    protocol_version: str = PROTOCOL_VERSION
    model_id: str = ""
    modes: dict[str, ModeMetrics]
    macro_f1: float = Field(ge=0.0, le=1.0)
    macro_auc: float = Field(ge=0.0, le=1.0)
    total_cases: int = Field(ge=0)
    verified: bool


def _metrics_from_counts(mode: ThreatMode, *, tp: int, fp: int, fn: int, tn: int, scores: Sequence[float] = ()) -> ModeMetrics:
    values = (tp, fp, fn, tn)
    if any(isinstance(v, bool) or v < 0 for v in values):
        raise ValueError("confusion counts must be non-negative integers")
    if not scores:
        scores = [1.0] * tp + [1.0] * fp + [0.0] * fn + [0.0] * tn
    if len(scores) != tp + fp + fn + tn:
        raise ValueError("scores must align with counts")
    labels = [1] * tp + [0] * fp + [1] * fn + [0] * tn
    cases = tp + fp + fn + tn
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    ci_low, ci_high = wilson_interval(tp + tn, cases)
    return ModeMetrics(
        mode=mode,
        tp=tp, fp=fp, fn=fn, tn=tn,
        precision=round(precision, 6), recall=round(recall, 6), f1=round(f1, 6),
        accuracy=round((tp + tn) / cases if cases else 0.0, 6),
        auc=roc_auc(labels, scores), cases=cases, ci_low=ci_low, ci_high=ci_high,
    )


# -- evaluation --------------------------------------------------------------
async def run_eval(cases: Iterable[EvalCase], engine: BengalEngine, *, model_id: str = "") -> EvalReport:
    case_list = tuple(cases)
    results: list[CaseResult] = []
    score_buckets: dict[ThreatMode, dict[str, list[float]]] = {m: {k: [] for k in ("tp", "fp", "fn", "tn")} for m in ThreatMode}
    counts: dict[ThreatMode, dict[str, int]] = {m: {k: 0 for k in ("tp", "fp", "fn", "tn")} for m in ThreatMode}
    for case in case_list:
        analyzer_case = BengalCase(
            case_id=case.case_id,
            text=case.text,
            trusted_corpus=case.trusted_corpus,
            claims=case.claims,
            sources=case.sources,
            perspectives=case.perspectives,
            records=case.records,
            query=case.query,
            response=case.response,
        )
        report = await engine.analyze(analyzer_case, modes=(case.mode,))
        predicted, score = predict(report, case.mode)
        correct = predicted == case.expected_label
        results.append(CaseResult(case_id=case.case_id, mode=case.mode, expected=case.expected_label, predicted=predicted, correct=correct, score=score))
        expected_positive = case.expected_label == POSITIVE_LABEL[case.mode]
        predicted_positive = predicted == POSITIVE_LABEL[case.mode]
        bucket = "tp" if expected_positive and predicted_positive else "fp" if not expected_positive and predicted_positive else "fn" if expected_positive and not predicted_positive else "tn"
        counts[case.mode][bucket] += 1
        score_buckets[case.mode][bucket].append(score)

    metrics = {
        mode.value: _metrics_from_counts(
            mode,
            tp=counts[mode]["tp"], fp=counts[mode]["fp"], fn=counts[mode]["fn"], tn=counts[mode]["tn"],
            scores=score_buckets[mode]["tp"] + score_buckets[mode]["fp"] + score_buckets[mode]["fn"] + score_buckets[mode]["tn"],
        )
        for mode in ThreatMode
    }
    verified = (
        len(results) == len(case_list)
        and all(result.case_id == case.case_id for result, case in zip(results, case_list))
        and sum(item.cases for item in metrics.values()) == len(case_list)
        and all(item.tp + item.fp + item.fn + item.tn == item.cases for item in metrics.values())
        and all(result.predicted in (POSITIVE_LABEL[result.mode], NEGATIVE_LABEL[result.mode]) for result in results)
    )
    order = tuple(m.value for m in ThreatMode)
    return EvalReport(
        protocol_version=PROTOCOL_VERSION,
        model_id=model_id,
        modes=metrics,
        macro_f1=round(sum(metrics[k].f1 for k in order) / len(order), 6),
        macro_auc=round(sum(metrics[k].auc for k in order) / len(order), 6),
        total_cases=len(case_list),
        verified=verified,
    )


def load_cases(path: str | Path) -> tuple[EvalCase, ...]:
    source = Path(path)
    cases: list[EvalCase] = []
    seen: set[str] = set()
    for lineno, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
            if not isinstance(payload, dict):
                raise ValueError("record must be a JSON object")
            case = EvalCase.model_validate(payload)
        except Exception as exc:
            raise ValueError(f"{source} line {lineno}: invalid eval case: {exc}") from exc
        if case.case_id in seen:
            raise ValueError(f"{source} line {lineno}: duplicate case_id {case.case_id!r}")
        seen.add(case.case_id)
        cases.append(case)
    if not cases:
        raise ValueError(f"{source}: no eval cases")
    return tuple(cases)


__all__ = [
    "PROTOCOL_VERSION", "POSITIVE_LABEL", "NEGATIVE_LABEL", "ThreatMode", "EvalCase",
    "CaseResult", "ModeMetrics", "EvalReport", "wilson_interval", "roc_auc", "predict", "run_eval", "load_cases",
]
