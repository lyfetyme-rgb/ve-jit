"""BENGAL BAA analyzers — four-topic threat assessment, observation only.

Redacted outputs are safe for the audit ledger, health endpoint and UI:
opaque ids, 16-hex digests, counts, scores and reason codes only. No analyzer
has a dependency on the database, policy gate or execution tools.
"""

import hashlib
import hmac
import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.pci_guard import redact as pci_redact

# -- shared primitives -------------------------------------------------------
class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ThreatMode(str, Enum):
    BIAS = "bias"
    HALLUCINATION = "hallucination"
    SAFE_INFO_FLOW = "safe_info_flow"
    POISONED_SOURCE = "poisoned_source"


class Severity(str, Enum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class BiasDimension(str, Enum):
    COGNITIVE = "cognitive"
    DEMOGRAPHIC = "demographic"
    IDEOLOGICAL = "ideological"
    CULTURAL = "cultural"
    TEMPORAL = "temporal"


class ClaimStatus(str, Enum):
    GROUNDED = "grounded"
    PLAUSIBLE_INFERENCE = "plausible_inference"
    SPURIOUS = "spurious"
    UNVERIFIABLE = "unverifiable"


class SourceIntent(str, Enum):
    INFORMATIONAL = "informational"
    PERSUASIVE = "persuasive"
    ADVERSARIAL = "adversarial"
    UNKNOWN = "unknown"


class Verdict(str, Enum):
    ALLOW = "allow"
    REVIEW = "review"
    BLOCK = "block"


_STOPWORDS = frozenset(
    "a an the and or but if then than of to in on at by for with from is are was were be been "
    "being this that these those it its as into about over under again further has have had do "
    "does did not no nor so such only own same too very can will just should now".split()
)
_TOKEN = re.compile(r"[a-z0-9]+")
_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")
_NUMERIC = re.compile(r"\b\d+(?:[.,]\d+)?%?\b")
_PERSUASIVE = re.compile(r"\b(clearly|obviously|undoubtedly|must|everyone knows|believe me|trust us)\b", re.I)
_ADVERSARIAL = re.compile(r"\b(attack|expose|leak|destroy|compromise|sabotage|infiltrate)\b", re.I)


def _tokens(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(str(text).lower()) if len(t) > 2 and t not in _STOPWORDS]


def _vector(text: str) -> Counter[str]:
    return Counter(_tokens(text))


def _cosine(left: Counter[str], right: Counter[str]) -> float:
    if not left or not right:
        return 0.0
    shared = set(left) & set(right)
    dot = sum(left[k] * right[k] for k in shared)
    left_norm = math.sqrt(sum(v * v for v in left.values()))
    right_norm = math.sqrt(sum(v * v for v in right.values()))
    return round(dot / (left_norm * right_norm), 6) if left_norm and right_norm else 0.0


def _jaccard(left: Counter[str], right: Counter[str]) -> float:
    union = set(left) | set(right)
    if not union:
        return 0.0
    return sum(min(left.get(k, 0), right.get(k, 0)) for k in union) / sum(max(left.get(k, 0), right.get(k, 0)) for k in union)


def fingerprint(text: Any) -> str:
    """Stable 16-hex opaque id; redacts cardholder material before hashing."""
    raw = text if isinstance(text, str) else repr(text)
    safe = pci_redact(raw)
    if not isinstance(safe, str):
        safe = str(safe)
    return hashlib.sha256(safe.encode("utf-8")).hexdigest()[:16]


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE.split(str(text)) if s.strip()]


def _severity(score: float, *, high: float = 0.7, medium: float = 0.4) -> Severity:
    if score >= high:
        return Severity.HIGH
    if score >= medium:
        return Severity.MEDIUM
    if score > 0:
        return Severity.LOW
    return Severity.NONE


def _max_severity(values: Any) -> Severity:
    rank = {Severity.NONE: 0, Severity.LOW: 1, Severity.MEDIUM: 2, Severity.HIGH: 3, Severity.CRITICAL: 4}
    items = list(values)
    return max(items, key=lambda s: rank[s]) if items else Severity.NONE


# -- topic 1: bias / diverse perspectives ------------------------------------
class BiasSignal(Strict):
    dimension: BiasDimension
    indicator_count: int = Field(ge=0)
    severity: Severity
    example_fingerprints: tuple[str, ...] = ()


class Perspective(Strict):
    label: str
    term_digests: tuple[str, ...]
    stance_vector_digest: str


class PerspectiveSpace(Strict):
    perspectives: tuple[Perspective, ...]
    diversity_score: float = Field(ge=0.0, le=1.0)
    pairwise_similarity: tuple[tuple[int, int, float], ...]
    dialogue_turns: int = 0
    perspective_shift: float = Field(ge=0.0, le=1.0)


class BiasAssessment(Strict):
    signals: tuple[BiasSignal, ...]
    bias_score: float = Field(ge=0.0, le=1.0)
    severity: Severity
    space: PerspectiveSpace
    method: str = "lexicon+perspective-vector"


_DEFAULT_LEXICONS: dict[str, tuple[str, ...]] = {
    BiasDimension.COGNITIVE.value: ("obviously", "clearly", "undoubtedly", "must", "everyone knows"),
    BiasDimension.DEMOGRAPHIC.value: ("those people", "they all", "every woman", "all men", "these folks"),
    BiasDimension.IDEOLOGICAL.value: ("traitor", "regime", "evil", "patriot", "enemy of the people"),
    BiasDimension.CULTURAL.value: ("civilized", "barbaric", "western values", "foreigners", "our way of life"),
    BiasDimension.TEMPORAL.value: ("always", "never", "historically proven", "forever", "since the beginning"),
}


def _lexicon_signals(text: str, lexicons: dict[str, tuple[str, ...]]) -> tuple[BiasSignal, ...]:
    lowered = text.lower()
    signals = []
    for dimension, terms in lexicons.items():
        hits = [t for t in terms if t in lowered]
        signals.append(
            BiasSignal(
                dimension=BiasDimension(dimension),
                indicator_count=len(hits),
                severity=_severity(min(1.0, len(hits) / 2), high=0.6, medium=0.3),
                example_fingerprints=tuple(fingerprint(t) for t in hits),
            )
        )
    return tuple(signals)


_PERSPECTIVE_FRAMES: dict[str, tuple[str, ...]] = {
    "analyst": ("signal", "correlate", "assess", "evidence"),
    "adversary": ("risk", "threat", "compromise", "exploit"),
    "auditor": ("verify", "control", "compliance", "trace"),
    "operator": ("impact", "contain", "recover", "availability"),
    "executive": ("cost", "decision", "exposure", "priority"),
}


def _reframe(text: str, label: str) -> str:
    terms = _PERSPECTIVE_FRAMES.get(label.lower(), ("perspective", "focus", "interpret", "context"))
    return f"{text} {' '.join(terms)}"


def _perspective_space(texts: list[str], labels: list[str], *, dialogue_turns: int = 0, shift: float = 0.0) -> PerspectiveSpace:
    vectors = [_vector(t) for t in texts]
    perspectives = tuple(
        Perspective(
            label=label,
            term_digests=tuple(fingerprint(term) for term, _ in vec.most_common(6)),
            stance_vector_digest=hashlib.sha256(json.dumps(sorted(vec.items())).encode()).hexdigest()[:16],
        )
        for label, vec in zip(labels, vectors)
    )
    pairs: list[tuple[int, int, float]] = []
    similarities: list[float] = []
    for i in range(len(vectors)):
        for j in range(i + 1, len(vectors)):
            score = _cosine(vectors[i], vectors[j])
            pairs.append((i, j, score))
            similarities.append(score)
    diversity = round(1.0 - (sum(similarities) / len(similarities)), 4) if similarities else 0.0
    return PerspectiveSpace(
        perspectives=perspectives,
        diversity_score=diversity,
        pairwise_similarity=tuple(pairs),
        dialogue_turns=dialogue_turns,
        perspective_shift=round(shift, 4),
    )


async def analyze_bias(
    text: str,
    *,
    llm: Any | None = None,
    perspectives: tuple[str, ...] = (),
    lexicons: dict[str, tuple[str, ...]] | None = None,
    simulate_dialogue: bool = True,
) -> BiasAssessment:
    signals = _lexicon_signals(text, lexicons or _DEFAULT_LEXICONS)
    bias_score = min(1.0, sum(s.indicator_count for s in signals) / 4)
    labels = list(perspectives) or ["analyst", "adversary", "auditor"]
    texts: list[str] = []
    shift = 0.0
    turns = 0
    if llm is not None:
        for label in labels:
            prompt = (
                f"Rewrite the following text from the perspective of a {label}. "
                "Keep the same facts. Return only the rewrite.\n\nTEXT: " + text
            )
            try:
                response = await llm.complete(
                    system="You are a perspective simulator.", messages=[{"role": "user", "content": prompt}]
                )
                rewritten = str(getattr(response, "content", response))
                texts.append(rewritten if rewritten.strip() else _reframe(text, label))
            except Exception:
                texts.append(_reframe(text, label))
    else:
        texts = [_reframe(text, label) for label in labels]

    if simulate_dialogue and len(labels) >= 2:
        first = [_vector(t) for t in texts]
        last: list[Counter[str]] = []
        for index, label in enumerate(labels[:3]):
            other = labels[(index + 1) % len(labels)]
            if llm is not None:
                prompt = f"As a {label}, reply briefly to this {other} claim: {texts[(index + 1) % len(texts)]}"
                try:
                    response = await llm.complete(
                        system="You are in a simulated analytical dialogue.",
                        messages=[{"role": "user", "content": prompt}],
                    )
                    reply = str(getattr(response, "content", response)) or _reframe(texts[index], other)
                except Exception:
                    reply = _reframe(texts[index], other)
            else:
                reply = _reframe(texts[index], other)
            turns += 1
            last.append(_vector(reply))
        shift = round(sum(1.0 - _cosine(first[i], last[i]) for i in range(len(last))) / len(last), 4) if last else 0.0

    return BiasAssessment(
        signals=signals,
        bias_score=round(bias_score, 4),
        severity=_severity(bias_score),
        space=_perspective_space(texts, labels, dialogue_turns=turns, shift=shift),
    )


# -- topic 2: hallucination / inference --------------------------------------
class ClaimAssessment(Strict):
    claim_fingerprint: str
    support: float = Field(ge=0.0, le=1.0)
    status: ClaimStatus
    corpus_fingerprint: str | None = None
    reason_codes: tuple[str, ...]


class ConfidenceBasis(Strict):
    decision_coverage: float
    corpus_coverage: float
    decision_margin: float
    weights: dict[str, float]


class HallucinationAssessment(Strict):
    claims: tuple[ClaimAssessment, ...]
    grounded_count: int = Field(ge=0)
    spurious_count: int = Field(ge=0)
    plausible_inference_count: int = Field(ge=0)
    unverifiable_count: int = Field(ge=0)
    hallucination_rate: float = Field(ge=0.0, le=1.0)
    inference_rate: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    confidence_basis: ConfidenceBasis
    severity: Severity
    method: str = "grounded-overlap-with-inference-allowance"


def analyze_hallucination(
    claims: list[str] | tuple[str, ...],
    *,
    trusted_corpus: list[str] | tuple[str, ...] = (),
    grounded_min: float = 0.6,
    plausible_min: float = 0.25,
) -> HallucinationAssessment:
    corpus = [_sentences(c) for c in trusted_corpus]
    flat = [s for group in corpus for s in group]
    corpus_vectors = [_vector(s) for s in flat]
    assessed: list[ClaimAssessment] = []
    for claim in claims:
        vec = _vector(claim)
        best, best_index = 0.0, -1
        for idx, cv in enumerate(corpus_vectors):
            score = _jaccard(vec, cv)
            if score > best:
                best, best_index = score, idx
        codes: list[str] = []
        specificity = set(_NUMERIC.findall(claim))
        best_text = flat[best_index] if best_index >= 0 else ""
        unsupported = {value for value in specificity if value not in best_text}
        if unsupported:
            status, codes = ClaimStatus.SPURIOUS, ["UNSUPPORTED_SPECIFICITY"]
        elif best >= grounded_min:
            status, codes = ClaimStatus.GROUNDED, ["CORPUS_OVERLAP_HIGH"]
        elif best >= plausible_min:
            status = ClaimStatus.PLAUSIBLE_INFERENCE
            codes = ["CORPUS_OVERLAP_MODERATE", "INFERENCE_ALLOWED"]
        else:
            status, codes = ClaimStatus.UNVERIFIABLE, ["NO_CORPUS_SUPPORT"]
        assessed.append(
            ClaimAssessment(
                claim_fingerprint=fingerprint(claim),
                support=best,
                status=status,
                corpus_fingerprint=(fingerprint(flat[best_index]) if best_index >= 0 else None),
                reason_codes=tuple(codes),
            )
        )

    total = len(assessed) or 1
    grounded = sum(a.status is ClaimStatus.GROUNDED for a in assessed)
    spurious = sum(a.status is ClaimStatus.SPURIOUS for a in assessed)
    plausible = sum(a.status is ClaimStatus.PLAUSIBLE_INFERENCE for a in assessed)
    unverifiable = sum(a.status is ClaimStatus.UNVERIFIABLE for a in assessed)
    decided = grounded + spurious
    weights = {"decision_coverage": 0.4, "corpus_coverage": 0.3, "decision_margin": 0.3}
    confidence = round(
        weights["decision_coverage"] * (decided / total)
        + weights["corpus_coverage"] * (sum(1 for a in assessed if a.corpus_fingerprint) / total)
        + weights["decision_margin"]
        * min(1.0, (sum(min(abs(a.support - grounded_min), abs(a.support - plausible_min)) for a in assessed) / total) * 2),
        4,
    )
    rate = round(spurious / total, 4)
    return HallucinationAssessment(
        claims=tuple(assessed),
        grounded_count=grounded,
        spurious_count=spurious,
        plausible_inference_count=plausible,
        unverifiable_count=unverifiable,
        hallucination_rate=rate,
        inference_rate=round(plausible / total, 4),
        confidence=confidence,
        confidence_basis=ConfidenceBasis(
            decision_coverage=round(decided / total, 4),
            corpus_coverage=round(sum(1 for a in assessed if a.corpus_fingerprint) / total, 4),
            decision_margin=round(
                sum(min(abs(a.support - grounded_min), abs(a.support - plausible_min)) for a in assessed) / total, 4
            ),
            weights=weights,
        ),
        severity=_severity(rate),
    )


# -- topic 3: safe information flow ------------------------------------------
class SensitiveFinding(Strict):
    category: str
    count: int = Field(ge=0)


class SanitizationManifest(Strict):
    original_fingerprint: str
    sanitized_fingerprint: str
    categories: tuple[str, ...]
    operations: tuple[str, ...]
    mac: str

    def verify(self, *, secret: str, original: str, sanitized: str) -> bool:
        expected = _sanitization_mac(
            secret=secret,
            original=original,
            sanitized=sanitized,
            categories=list(self.categories),
            operations=list(self.operations),
        )
        return hmac.compare_digest(expected, self.mac)


def _sanitization_mac(*, secret: str, original: str, sanitized: str, categories: list[str], operations: list[str]) -> str:
    body = json.dumps(
        [fingerprint(original), fingerprint(sanitized), sorted(categories), sorted(operations)], separators=(",", ":")
    ).encode("utf-8")
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


_SENSITIVE_PATTERNS: dict[str, re.Pattern] = {
    "email": re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),
    "phone": re.compile(r"\b\+?\d[\d\-\s()]{7,}\d\b"),
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "mrn": re.compile(r"\b(?:MRN|MRN-)\s*\d{3,}\b", re.I),
    "diagnosis": re.compile(r"\b(?:diagnosis|condition-alpha)\b", re.I),
}


def detect_sensitive_data(text: str) -> list[str]:
    labels = sorted(name for name, pattern in _SENSITIVE_PATTERNS.items() if pattern.search(text))
    return labels


def sanitize_text(text: str, *, secret: str = "") -> tuple[str, SanitizationManifest]:
    findings = detect_sensitive_data(text)
    for pattern in _SENSITIVE_PATTERNS.values():
        text = pattern.sub("[REDACTED]", text)
    sanitized = str(pci_redact(text))
    operations = tuple(sorted({f"{c}->redacted" for c in findings}))
    return sanitized, SanitizationManifest(
        original_fingerprint=fingerprint(text)[:16],
        sanitized_fingerprint=fingerprint(sanitized)[:16],
        categories=tuple(findings),
        operations=operations,
        mac=_sanitization_mac(secret=secret, original=text, sanitized=sanitized, categories=findings, operations=list(operations)),
    )


class AggregationRisk(Strict):
    group_fingerprint: str
    group_size: int = Field(ge=0)
    k_threshold: int = Field(ge=1)
    reason: str


class AccessAssessment(Strict):
    likelihood: float = Field(ge=0.0, le=1.0)
    verdict: Verdict
    reason_codes: tuple[str, ...]


class SafeFlowAssessment(Strict):
    findings: tuple[SensitiveFinding, ...]
    sanitized_fingerprint: str
    manifest: SanitizationManifest
    aggregation_risks: tuple[AggregationRisk, ...]
    access: AccessAssessment
    severity: Severity
    method: str = "verifiable-redaction+aggregation+access-likelihood"


_ACCESS_PATTERNS: tuple[tuple[str, re.Pattern[str], float], ...] = (
    ("BULK_EXPORT", re.compile(r"\b(export|dump|list all|download)\b", re.I), 0.6),
    ("DIRECT_IDENTIFIER", re.compile(r"\b(ssn|mrn|passport|social security)\b", re.I), 1.0),
    ("CROSS_RECORD", re.compile(r"\b(every|all) (patient|customer|user|record)s?\b", re.I), 0.8),
    ("SENSITIVE_FIELD", re.compile(r"\b(diagnosis|medication|salary|tax id)\b", re.I), 0.5),
)


def assess_access(query: str, response: str, *, review_at: float = 0.5, block_at: float = 0.8) -> AccessAssessment:
    blob = f"{query}\n{response}"
    matches = [(name, weight) for name, pattern, weight in _ACCESS_PATTERNS if pattern.search(blob)]
    peak = max((weight for _, weight in matches), default=0.0)
    score = round(min(1.0, peak + 0.15 * max(0, len(matches) - 1)), 4)
    verdict = Verdict.BLOCK if score >= block_at else Verdict.REVIEW if score >= review_at else Verdict.ALLOW
    return AccessAssessment(likelihood=score, verdict=verdict, reason_codes=tuple(name for name, _ in matches))


def detect_aggregation_risks(records: Any, *, k_threshold: int = 3) -> tuple[AggregationRisk, ...]:
    groups: dict[str, set[str]] = defaultdict(set)
    sizes: Counter[str] = Counter()
    for record in records:
        quasi = [str(record.get(k, "")) for k in ("zip", "dob", "gender", "role", "department") if k in record]
        if not quasi:
            continue
        key = fingerprint("|".join(sorted(quasi)))
        sizes[key] += 1
        sensitive = str(record.get("diagnosis") or record.get("salary") or record.get("ssn") or record.get("secret") or "")
        if sensitive:
            groups[key].add(fingerprint(sensitive))
    risks = [
        AggregationRisk(group_fingerprint=key, group_size=sizes[key], k_threshold=k_threshold, reason="SMALL_CELL_REIDENTIFICATION")
        for key, values in groups.items()
        if 0 < sizes[key] < k_threshold or len(values) == 1
    ]
    return tuple(risks)


def analyze_safe_flow(
    text: str,
    *,
    secret: str = "",
    records: Any = (),
    query: str = "",
    response: str = "",
    k_threshold: int = 3,
) -> SafeFlowAssessment:
    original_text = text
    findings = tuple(SensitiveFinding(category=c, count=1) for c in detect_sensitive_data(text))
    sanitized, manifest = sanitize_text(text, secret=secret)
    risks = detect_aggregation_risks(records, k_threshold=k_threshold)
    access = assess_access(query or original_text, response)
    score = max(
        [min(1.0, len(findings) / 3)]
        + [1.0 if r.group_size < r.k_threshold else 0.5 for r in risks]
        + [{"allow": 0.0, "review": 0.6, "block": 1.0}[access.verdict.value]]
    )
    return SafeFlowAssessment(
        findings=findings,
        sanitized_fingerprint=manifest.sanitized_fingerprint,
        manifest=manifest,
        aggregation_risks=risks,
        access=access,
        severity=_severity(score),
    )


# -- topic 4: poisoned / imperfect sources -----------------------------------
class SourceDocument(Strict):
    source_id: str
    text: str
    provenance: tuple[str, ...] = ()
    historical_accuracy: float | None = Field(default=None, ge=0.0, le=1.0)

    @field_validator("source_id")
    @classmethod
    def _bounded(cls, value: str) -> str:
        if not value or len(value) > 64:
            raise ValueError("source_id must be 1-64 characters")
        return value


class SourceReliability(Strict):
    source_id: str
    score: float = Field(ge=0.0, le=1.0)
    factors: dict[str, float]
    intent: SourceIntent
    intent_confidence: float = Field(ge=0.0, le=1.0)
    severity: Severity


class Corroboration(Strict):
    claim_fingerprint: str
    independent_sources: int = Field(ge=0)
    support_score: float = Field(ge=0.0, le=1.0)
    verdict: str
    conflicting_sources: int = Field(ge=0)


class ReliableIntel(Strict):
    claim_fingerprint: str
    support_score: float = Field(ge=0.0, le=1.0)
    source_count: int = Field(ge=0)
    status: str


class SourceResilienceAssessment(Strict):
    reliabilities: tuple[SourceReliability, ...]
    corroborations: tuple[Corroboration, ...]
    intel: tuple[ReliableIntel, ...]
    gaps: tuple[str, ...]
    severity: Severity
    method: str = "provenance-independence+corroboration"


def _intent(text: str) -> tuple[SourceIntent, float]:
    persuasive = len(_PERSUASIVE.findall(text))
    adversarial = len(_ADVERSARIAL.findall(text))
    if adversarial >= persuasive and adversarial:
        return SourceIntent.ADVERSARIAL, round(min(1.0, adversarial / 3), 4)
    if persuasive:
        return SourceIntent.PERSUASIVE, round(min(1.0, persuasive / 3), 4)
    return SourceIntent.INFORMATIONAL, 0.9


def analyze_sources(
    sources: Any,
    *,
    min_independent: int = 2,
    corroboration_min: float = 0.5,
) -> SourceResilienceAssessment:
    reliabilities: list[SourceReliability] = []
    claim_vectors: dict[str, list[tuple[Counter[str], str]]] = defaultdict(list)
    for source in sources:
        provenance_completeness = min(1.0, len(source.provenance) / 2)
        accuracy = source.historical_accuracy if source.historical_accuracy is not None else 0.5
        internal = min(1.0, len(_sentences(source.text)) / 2)
        intent, intent_conf = _intent(source.text)
        intent_factor = 0.3 if intent is SourceIntent.ADVERSARIAL else 1.0
        score = round((0.3 * provenance_completeness + 0.4 * accuracy + 0.3 * internal) * intent_factor, 4)
        reliabilities.append(
            SourceReliability(
                source_id=source.source_id,
                score=score,
                factors={
                    "provenance_completeness": round(provenance_completeness, 4),
                    "historical_accuracy": round(accuracy, 4),
                    "internal_consistency": round(internal, 4),
                },
                intent=intent,
                intent_confidence=intent_conf,
                severity=_severity(1.0 - score),
            )
        )
        for sentence in _sentences(source.text):
            claim_vectors[fingerprint(sentence)].append((_vector(sentence), source.provenance[0] if source.provenance else source.source_id))

    corroborations: list[Corroboration] = []
    intel: list[ReliableIntel] = []
    gaps: list[str] = []
    for fp, entries in claim_vectors.items():
        roots = {root for _, root in entries}
        independent = len(roots)
        support = max((_cosine(entries[i][0], entries[j][0]) for i in range(len(entries)) for j in range(i + 1, len(entries))), default=0.0)
        conflicts = sum(
            1
            for i in range(len(entries))
            for j in range(i + 1, len(entries))
            if _cosine(entries[i][0], entries[j][0]) < 0.1
        )
        verdict = (
            "corroborated"
            if independent >= min_independent and support >= corroboration_min
            else "conflicted"
            if conflicts
            else "single_source"
            if independent < min_independent
            else "unverifiable"
        )
        corroborations.append(
            Corroboration(claim_fingerprint=fp, independent_sources=independent, support_score=round(support, 4), verdict=verdict, conflicting_sources=conflicts)
        )
        if verdict == "corroborated":
            intel.append(ReliableIntel(claim_fingerprint=fp, support_score=round(support, 4), source_count=independent, status="extracted"))
        else:
            gaps.append(fp)

    risk = sum(1 for r in reliabilities if r.severity in (Severity.HIGH, Severity.CRITICAL))
    risk += sum(1 for c in corroborations if c.verdict == "conflicted")
    return SourceResilienceAssessment(
        reliabilities=tuple(reliabilities),
        corroborations=tuple(corroborations),
        intel=tuple(intel),
        gaps=tuple(gaps),
        severity=_severity(min(1.0, risk / 3)),
    )


# -- composed report ---------------------------------------------------------
@dataclass(frozen=True)
class BengalThresholds:
    grounded_min: float = 0.6
    plausible_min: float = 0.25
    min_independent_sources: int = 2
    corroboration_min: float = 0.5
    k_anonymity: int = 3
    review_at: float = 0.5
    block_at: float = 0.8


class BengalCase(Strict):
    case_id: str
    text: str
    tenant_id: str = "tenant_001"
    trusted_corpus: tuple[str, ...] = ()
    claims: tuple[str, ...] = ()
    sources: tuple[SourceDocument, ...] = ()
    perspectives: tuple[str, ...] = ()
    records: tuple[dict[str, Any], ...] = ()
    query: str = ""
    response: str = ""


class BengalReport(Strict):
    case_id: str
    tenant_id: str
    modes: tuple[ThreatMode, ...]
    input_fingerprint: str
    bias: Any = None
    hallucination: Any = None
    safe_flow: Any = None
    sources: Any = None
    severity: Severity = Severity.NONE
    method_version: str = "bengal-baa/v1"

    def summary(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "tenant_id": self.tenant_id,
            "severity": self.severity.value,
            "modes": [m.value for m in self.modes],
            "bias_score": self.bias.bias_score if self.bias else None,
            "bias_diversity": self.bias.space.diversity_score if self.bias else None,
            "hallucination_rate": self.hallucination.hallucination_rate if self.hallucination else None,
            "inference_rate": self.hallucination.inference_rate if self.hallucination else None,
            "confidence": self.hallucination.confidence if self.hallucination else None,
            "safe_flow_severity": self.safe_flow.severity.value if self.safe_flow else None,
            "sensitive_categories": [f.category for f in self.safe_flow.findings] if self.safe_flow else [],
            "access_verdict": self.safe_flow.access.verdict.value if self.safe_flow else None,
            "unreliable_sources": (
                [r.source_id for r in self.sources.reliabilities if r.severity in (Severity.HIGH, Severity.CRITICAL)]
                if self.sources
                else []
            ),
            "corroborated_claims": sum(i.status == "extracted" for i in self.sources.intel) if self.sources else 0,
        }


class BengalEngine:
    """Composable analyzer engine; inject any LLM to generalise across models."""

    def __init__(
        self,
        *,
        llm: Any | None = None,
        secret: str = "",
        thresholds: BengalThresholds | None = None,
        lexicons: dict[str, tuple[str, ...]] | None = None,
    ) -> None:
        self.llm = llm
        self.secret = secret
        self.thresholds = thresholds or BengalThresholds()
        self.lexicons = lexicons or _DEFAULT_LEXICONS

    async def analyze(
        self,
        case: BengalCase,
        *,
        modes: Any = None,
    ) -> BengalReport:
        selected = tuple(modes) if modes is not None else tuple(ThreatMode)
        t = self.thresholds
        bias = hallucination = safe_flow = sources = None
        if ThreatMode.BIAS in selected:
            bias = await analyze_bias(
                case.text, llm=self.llm, perspectives=case.perspectives, lexicons=self.lexicons
            )
        if ThreatMode.HALLUCINATION in selected:
            hallucination = analyze_hallucination(
                case.claims or tuple(_sentences(case.text)),
                trusted_corpus=case.trusted_corpus,
                grounded_min=t.grounded_min,
                plausible_min=t.plausible_min,
            )
        if ThreatMode.SAFE_INFO_FLOW in selected:
            safe_flow = analyze_safe_flow(
                case.text,
                secret=self.secret,
                records=case.records,
                query=case.query,
                response=case.response,
                k_threshold=t.k_anonymity,
            )
        if ThreatMode.POISONED_SOURCE in selected:
            sources = analyze_sources(
                case.sources, min_independent=t.min_independent_sources, corroboration_min=t.corroboration_min
            )
        severities = [a.severity for a in (bias, hallucination, safe_flow, sources) if a is not None]
        return BengalReport(
            case_id=case.case_id,
            tenant_id=case.tenant_id,
            modes=selected,
            input_fingerprint=fingerprint(case.text),
            bias=bias,
            hallucination=hallucination,
            safe_flow=safe_flow,
            sources=sources,
            severity=_max_severity(severities),
        )


__all__ = [
    "ThreatMode", "Severity", "BiasDimension", "ClaimStatus", "SourceIntent", "Verdict",
    "BiasAssessment", "PerspectiveSpace", "HallucinationAssessment", "SafeFlowAssessment",
    "SanitizationManifest", "SourceDocument", "SourceResilienceAssessment",
    "BengalThresholds", "BengalCase", "BengalReport", "BengalEngine", "fingerprint",
    "analyze_bias", "analyze_hallucination", "analyze_safe_flow", "analyze_sources",
]
