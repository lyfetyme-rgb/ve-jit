# sync-go

Stateless Go sidecars for the Vendarix edge sync pipeline. Nothing here touches
the operational or audit databases; the Python edge owns all state, policy and
audit logging.

```
sync-go/
├─ cmd/vejit-sync       # CLI entry point: classify deltas against cloud baseline
│  └─ main.go
├─ internal/classifier  # commutative delta classifier (SALE vs RESTOCK vs …)
│  ├─ classify.go
│  └─ classify_test.go
├─ go.mod
└─ Makefile
```

## vejit-sync

Reads a JSONL snapshot of pending outbox deltas (emitted by the Python edge at
`GET /api/v1/edge/outbox/deltas`) plus a baseline (sku → on-hand at last watermark)
and emits one verdict per delta as JSONL for the gated recovery flow:

```
vejit-sync --baseline baseline.json --input outbox.jsonl --out verdicts.jsonl
```

Verdicts:
- `COMMUTE_APPLY` — pure SALE batch, aggregate-safe
- `QUARANTINE` — mixed/oversell/unknown; routes to the HITL quarantine queue

## Layout rationale

`internal/` hides the classifier from importers outside this module — callers
drive it via the `vejit-sync` binary or, if needed, import the module path once
the contract stabilizes. `cmd/` contains only wiring (flag parsing, io), never
business logic.
