# BENGAL BAA — Independent Assurance (IARPA BAA-24-03)

VE-JIT's fourth plane of scrutiny is **independent assurance**: it observes
models, events and recommendations but never approves, denies or executes
anything. Adapted from the USOC mesh implementation
(`agents/bengal.py`, `agents/bengal_eval.py`) into
`backend/app/assurance/`, it keeps the original invariants:

- **Observation only.** BENGAL reports are input to policy / HITL / egress
  controls; they are never substituted for those controls. No route in
  `app/api/assurance.py` writes to the database or calls an execution gate.
- **No raw payloads.** Reports carry only case ids, 16-hex fingerprints,
  counts, scores and reason codes — safe for the audit ledger, `/health`
  and the dashboard.
- **Model agnostic.** The deterministic lexical/statistical path works
  offline; an optional LLM can be injected for perspective/dialogue without
  changing the scoring contract.
- **Scoped opt-in.** `/api/v1/assurance/bengal/status` exposes only static
  capability metadata. Analysis runs only when explicitly requested.

## Four threat modes

| Mode | Analyzer | Redacted output |
|---|---|---|
| `bias` | lexicon signals + perspective vectors; optional LLM perspective/dialogue | bias score, diversity score, pairwise similarity, perspective shift, indicator counts, 16-hex fingerprints |
| `hallucination` | trusted-corpus overlap with plausible-inference allowance and unsupported-specificity check | per-claim status, support, hallucination/inference rates, confidence with coverage/margin basis |
| `safe_info_flow` | verifiable redaction, small-cell aggregation detection, sensitive-access likelihood | HMAC sanitization manifest (categories + digests only), group fingerprints, access verdict |
| `poisoned_source` | provenance, historical accuracy, intent, independent corroboration | source reliability factors, intent, corroboration verdicts, reliable-intel fingerprints |

## API

- `GET /api/v1/assurance/bengal/status` — method version, protocol version, mode list, observation-only flag
- `POST /api/v1/assurance/bengal/analyze` — `{case, modes}` → redacted `summary`
- `POST /api/v1/assurance/bengal/eval` — runs the fixed labeled corpus, returns per-mode confusion counts, precision/recall/F1, tie-aware rank AUC and Wilson 95% bounds, macro F1/AUC; optional `min_f1` gate fails with 422

## T&E protocol

`python -m app.assurance --min-f1 0.85` (from `backend/`) runs the closed
protocol: fixed 16-case JSONL corpus at `fixtures/bengal/cases.jsonl`,
fixed analyzer, each case in its own mode only, verified count reconciliation.
The corpus is calibrated to the analyzer's rules — the score is reproducible,
not a claim of real-world generalization. No raw case text reaches the report.

## Boundaries

- Cardholder data in a case body is rejected before parsing (`app.pci_guard`).
- BENGAL does **not** change policy gates, approvals, authorization decisions or
  playbook execution; it has no write access to commerce/operations tables.
- The browser dashboard shows only method version, mode count and T&E protocol
  version from the public health payload; no case data is published.
