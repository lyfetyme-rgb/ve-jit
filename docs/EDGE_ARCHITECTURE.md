# Edge Architecture — the Operational Engine

This document is the contract for the Vendarix edge node in VE-JIT. It
implements the platform specification's edge section: the node receives
hardware and enterprise events, normalizes them into shared contracts,
validates them, runs approved local AI skills, applies deterministic business
policies, requests human authorization where required, persists authorized
operational changes, queues events for cloud synchronization, and recovers /
reconciles after connectivity returns.

> Core rule inherited from the planes: **Intelligence produces
> recommendations; Operations executes authorized business actions; Commerce
> remains responsible for the customer-facing transaction.** The edge never
> bypasses that governance — edge-triggered business changes flow through the
> same approval and execution gates.

## Edge node capabilities

| Capability | Implementation |
|---|---|
| Receive hardware and enterprise events | `POST /api/v1/edge/ingest` (+ `/devices/heartbeat`) |
| Normalize into shared event contracts | Vendarix manifest — `app/edge/contracts.py` |
| Validate events before processing | `contracts.validate_manifest` — rejects, quarantines, never processes |
| Run approved local AI skills | `app/edge/skills.py` + `skills/<name>/skill.py` (approved only) |
| Apply deterministic business policies | `app/edge/policy.py` — plain Python, no model in the loop |
| Request human authorization where required | `edge-fabric` creates `AUTHORIZATION_REQUEST` recommendations on HOLD |
| Persist authorized operational changes | Event store + transactional outbox; plane ledgers; cycle-count execution |
| Queue events for cloud synchronization | `edge_outbox` (transactional) + `/outbox/drain` |
| Recover and reconcile after connectivity returns | `app/edge/recovery.py` — verify → replay → resolve → cloud ack |

## Hardware adapter layer

The core never parses proprietary device payloads. Adapters translate them
into the shared manifest; anything unrecognized becomes `edge.unrecognized`
with the raw payload hash-preserved — nothing is dropped and nothing guessed.

| Hardware ecosystem | Adapter | Interface(s) |
|---|---|---|
| NCR Voyix | `app/edge/adapters/ncr_voyix.py` | `ncr-voyix/pos-v1` — TX_COMPLETE, PRICE_QUERY, VOID, HEARTBEAT |
| Toshiba Global Commerce Solutions | `app/edge/adapters/toshiba_tcgs.py` | `toshiba-tcgs/upos-javapos-v1` — UPOS.TRANSACTION_COMPLETE / VOID_TRANSACTION, JPOS.ITEM_SOLD (legacy IBM/Toshiba) |
| Diebold Nixdorf | `app/edge/adapters/diebold_nixdorf.py` | `diebold-nixdorf/beetle-v1` — BEETLE_SALE / ITEM_VOID / HEARTBEAT |
| Scanner-scale (bi-optic) | `app/edge/adapters/scanner_scale.py` | `scanner-scale/bi-optic-v1` — WEIGHT_STABLE, ITEM_SCANNED, SCALE_CALIBRATED, LABEL_PRINTED |
| ERP / WMS / suppliers (enterprise) | `app/edge/adapters/enterprise.py` | `enterprise/erp-wms-supplier-v1` — declared `erp.*`, `wms.*`, `supplier.*`, `inventory.*` event types |

The adapter tables above document the **demo contracts** shipped with this
repository. Replace them with the banner's interface control documents before
hardware rollout. Each adapter is a pure, total function payload → manifests.

## The event fabric

```
HARDWARE / POS / ERP / WMS / SUPPLIERS
                  |
                  v
          EVENT INGESTION            POST /api/v1/edge/ingest
                  |
                  v
         SCHEMA VALIDATION           app/edge/contracts.py (schema_version pinned)
                  |
                  v
         NORMALIZATION               adapters emit Vendarix manifests (payload_hash re-verified)
                  |
                  v
         IDENTITY CHECK              tenant/store/device present + heartbeat healthy
                  |
                  v
          EVENT ROUTING              ROUTES map: pos.* -> operations, erp/wms -> operations,
                  |                  supplier.* -> intelligence
                  v
         POLICY (deterministic)      app/edge/policy.py
            /         \
           v           v
      ALLOW/FLAG    HOLD (requires_approval)
           |           |
           |           v
           |     AUTHORIZATION        recommendation kind AUTHORIZATION_REQUEST
           |           |              (human approves; Operations executes)
           v           v
        EVENT STORE  +  TRANSACTIONAL OUTBOX   (one DB transaction)
                  |
                  v
        CLOUD SYNCHRONIZATION        /api/v1/edge/outbox/drain
```

Every event carries the **eight required envelope metadata groups** (verified at
adapter output by `tests/test_guardrails.py::test_every_adapter_generates_complete_envelope`):

1. Tenant and store identity — `tenant_id`, `store_id`
2. Source system and device identity — `source_system`, `source_device_id`
3. Unique event id and idempotency key — `event_id`, `idempotency_key` (unique constraint — replays return the original event, `outcome=duplicate`)
4. Event type and schema version — `event_type`, `schema_version` (`vendarix.event.v1`)
5. Source sequence and timestamps — `source_seq`, `occurred_at`, `received_at`
6. Correlation and causation ids — `correlation_id`, `causation_id`
7. Payload hash — SHA-256 over canonical JSON
8. Processing and authorization metadata — `processing_metadata`, `auth_metadata`

Validation failures are stored with status `REJECTED` and the reasons — never silently dropped.
Hard-safety-gate failures are stored with status `BLOCKED`, authorization `DENY`, and are never queued.

## Database and persistence architecture

Six logical domains (implemented; mapping shown so future migrations know
exactly what moves):

| Domain | Implementation | Isolation |
|---|---|---|
| Event store | `edge_events` — immutable observed events (payload + hash) | **INSERT-only**, enforced in ORM (`app/edge/integrity.py`) and as DB triggers (`infrastructure/database/append_only.sql`) |
| Operational database | `inventory_items`, `purchase_orders`, `transfer_orders`, … (three planes) | Mutable current projections with a `version` column and compare-and-swap writes |
| Policy database | `edge_policy_rules` — named, versioned, activated/deactivated rules | See the hard safety gates below — the gate fails closed when no active rule exists or the bundle is stale |
| Intelligence database | `recommendations` — forecasts, recommendations, model outputs | Recommendations are never operational truth |
| Continuity database | `edge_continuity_records` (append-only), `edge_continuity_scores`, `edge_checkpoints`, `edge_recovery_sessions` | `edge_continuity_records` is INSERT-only |
| Audit ledger | `recommendations` lifecycle + `inventory_ledger` + `edge_events.auth_metadata` + `audit_logs/audit.log` | `inventory_ledger` is INSERT-only |

`edge_outbox` is the single deliberate exception to append-only: it is the
cloud-synchronization cursor (pending → synced / failed / quarantined), not the
audit trail. The audit trail is `edge_events` + `audit_logs/audit.log`.

Pipeline layers, never collapsed:

```
OBSERVED EVENT → NORMALIZED EVENT → OPERATIONAL TRANSACTION → CURRENT PROJECTION
```

AI recommendations are not operational truth. A recommendation cannot mutate
inventory or authorize a purchase; only Operations (with an approved
recommendation where required) executes.

## Storage isolation (core architectural guardrail)

**Six-domain separation.** The Event Store and Audit Ledger are physically
separated from the Operational Database at the application layer today, and
the invariants are enforced, not just documented:

1. **Append-only / INSERT-only enforcement.** `app/edge/integrity.py` attaches
   SQLAlchemy `before_update` / `before_delete` hooks to the event store and
   audit-ledger classes; any ORM attempt to mutate or delete them raises
   `AppendOnlyViolation`. `infrastructure/database/append_only.sql` ships the
   equivalent `BEFORE UPDATE / BEFORE DELETE` triggers for PostgreSQL and
   SQLite so the invariant holds even for non-ORM code paths.
2. **Optimistic concurrency.** Every mutable operational projection carries a
   `version` column; writes go through compare-and-swap
   (`WHERE id = :id AND version = :version` + version bump, see
   `app/planes/operations.py::cas_update`). Inventory quantity moves, purchase
   orders, transfers, orders and loyalty point balances are all CAS-guarded;
   conflicts raise `OptimisticConcurrencyError` → HTTP 409. A startup-time
   expand step (`ensure_version_columns`) adds the column to pre-guardrail
   databases idempotently.
3. **Skills are database-free by construction.** Python skills receive a
   serialized payload on stdin and return a validated envelope on stdout. They
   run with a sanitized environment (no `DATABASE_URL`, `REDIS_URL`, `KAFKA_*`,
   `CLOUD_*`, or token material), a hard timeout, a 1 MiB stdout cap, strict
   envelope validation, and no working-directory access to the platform
   source tree. `skills/requirements.txt` is deliberately empty (stdlib-only);
   `requirements-postgres.txt` holds the optional managed-lake driver and is
   **not** part of the default production requirements. `tools/validate_skills.py`
   fails CI if any banned import appears in a skill or if any dependency is
   declared in a skill's `pyproject.toml`.
4. **Recommended physical layout for scale:** separate attached SQLite files
   per domain (embedded) or PostgreSQL schemas per domain on managed servers,
   with INSERT-only permissions for the event store and audit ledger. The ORM
   boundary above is ready for either; the current single-file layout is kept
   for the demo and local development.

Implementation requirements — status:

| Requirement | Status |
|---|---|
| Transactional outbox | ✅ event + outbox row committed in one DB transaction |
| Unique constraints for idempotency | ✅ `edge_events.idempotency_key` unique |
| Append-only event history | ✅ enforced in ORM (`app/edge/integrity.py`) + DB triggers (`infrastructure/database/append_only.sql`) |
| Optimistic concurrency | ✅ `version` columns + compare-and-swap on inventory items, POs, transfers, orders, loyalty points, recommendations |
| Store/tenant partitioning | ✅ `store_id` + `tenant_id` on every event and plane table; per-store deployment is the recommended model |
| Schema evolution | ✅ `schema_version` pinned (`vendarix.event.v1`); `ensure_version_columns()` performs idempotent expand migrations for the new `version` columns |
| Replay checkpoints / watermarks | ✅ `edge_checkpoints` (`watermark_name=outbox`) advanced on cloud ack |
| Backup, retention, DR | ⚠️ documented in DEPLOYMENT_GUIDE; not automated here |

**Decision:** local edge DB and cloud enterprise DB are not one distributed
ACID database. They synchronize through explicit contracts: the outbox, the
watermark, and the recovery flow. The edge holds no cloud credentials.

## Continuity engine and state machine

```
STATE 1 ONLINE      normal edge-cloud communication
   |  connectivity degradation
STATE 2 DEGRADED    local execution; limited cloud communication
   |  cloud unavailable
STATE 3 OFFLINE     local policies, local ledger, durable event queue
   |  integrity or safety violation (from ANY state)
STATE 4 SAFE_MODE   restricted operations; unsafe actions blocked
   |
   +-- RECOVERING (explicit transition only — never automatic)
         connectivity_check → hash_verification → watermark_replay → cloud_ack
         → ONLINE   (any step failing moves the node to SAFE_MODE)
```

Enforced in `app/edge/continuity.py` + `app/edge/recovery.py`:

- SAFE_MODE is reachable from every state.
- **ONLINE is reachable only from RECOVERING** — never directly from ONLINE,
  DEGRADED, OFFLINE or SAFE_MODE — and only with the *latest* recovery session
  `COMPLETED`. There is no jump, no shortcut, no override.
- Recovery is a four-step ordered session (`edge_recovery_sessions`):

  1. `POST /api/v1/edge/recovery/connectivity-check` — cloud endpoint reachable,
     or `local_only` mode recorded when `CLOUD_SYNC_URL` is unset (ack must then
     use `ack_mode=local_override`)
  2. `POST /api/v1/edge/recovery/hash-verification` — every pending outbox event
     is re-validated and its payload hash re-verified
  3. `POST /api/v1/edge/recovery/watermark-replay` — contract replay from the
     checkpoint; duplicates and sequence gaps are reported for the ack step
  4. `POST /api/v1/edge/recovery/cloud-ack` — conflicts resolved
     (`quarantine` | `accept_local`), pending events acknowledged, watermark
     advances, session `COMPLETED`, transition → ONLINE
- Steps are enforced in order; calling them out of order returns 409. Any step
  failing moves the node to SAFE_MODE. `POST /recovery/reconcile` runs the same
  four steps in order for operators who want one command.
- Without a completed session the state machine refuses the ONLINE transition.
- `POST /recovery/verify` was removed in favour of the ordered session endpoints.

### Hard safety gates (two-step check)

Policy evaluation is two steps, in order (`app/edge/policy.py::evaluate_event`):

1. **Hard boolean invariants** — (a) not in SAFE_MODE, (b) policy bundle valid
   and fresh (≥1 active rule and newest rule younger than
   `POLICY_BUNDLE_MAX_AGE_SECONDS`, default 86400; 0 disables freshness check),
   (c) credentials valid (edge holds no cloud credentials; when
   `CLOUD_SYNC_URL` is set, `CLOUD_SYNC_TOKEN` must be present, otherwise the
   local-only mode is valid). Any failure → decision `DENY` **and the
   threshold rules are never consulted**. Events are stored `BLOCKED` with
   `authorization=DENY`; actions are refused with 409.
2. **Deterministic threshold rules** — only evaluated if every invariant
   passes; `requires_approval` rules force a human HOLD.

The numeric Continuity Value may be attached to the decision for observability
but carries **no veto and no permission**. It never authorizes an action.

SAFE_MODE and hard-gate blocks apply across the edge API and the operations
plane (adjustments, receiving, transfers, waste, PO approval, recommendation
execution) and to skill execution. Observation paths remain open. Two-step
gating is identical everywhere: invariants first, then thresholds.

### Continuity value

Computed and persisted (`edge_continuity_scores`, exposed at
`GET /api/v1/edge/status`). Weighted components — components not measured from
inside the process are reported as `not_measured`, never assumed healthy:

| Component | Weight | Source |
|---|---|---|
| database_integrity | 20 | quarantined outbox entries |
| queue_durability | 15 | pending/failed outbox depth |
| policy_freshness | 15 | active rules ≥ 3 and age ≤ 3600s |
| catalog_freshness | 10 | not measured (cloud-synchronized) |
| inventory_freshness | 10 | not measured (cloud-synchronized) |
| device_health | 15 | heartbeat health fraction |
| credential_validity | 10 | not measured (edge holds no cloud credentials) |
| sync_status | 5 | state machine |

**The continuity value never authorizes an action.** A high score cannot
override SAFE_MODE, an invalid policy, or an invalid credential.

## AI skills and polyglot execution

Skill execution contract (subprocess-isolated):

```
TypeScript run.ts ── JSON via stdin ──▶ Python skill.py ── AI/analytics ──▶ stdout JSON
       ▲                                                                  │
       └────────── validate + policy engine + authorized workflow ◀───────┘
```

- Python skills never receive database handles — they cannot write
  operational state.
- Only skills with `"approved": true` in `skills/<name>/config/default.json`
  execute (`GET /api/v1/edge/skills`, `POST .../skills/{name}/run`).
- Output passes through validation and the policy/authorization path like
  any other intelligence product.
- In this repository the same contract is executed via Python subprocess
  (`sys.executable skills/<name>/skill.py`) — byte-compatible with the
  stdin/stdout contract the future Node edge runtime (`run.ts`) drives.

Approved skills (`skills/`): `demand-forecasting`, `inventory-reconciliation`,
`spoilage-detection`, `shelf-intelligence`, `queue-optimization`,
`supplier-analysis`. Each ships `run.ts`, `package.json` (TypeScript runtime),
`pyproject.toml` (Python dependencies — stdlib only), `config/default.json`
(approval), `schemas/input.schema.json` / `output.schema.json`, and `tests/`.

### Language responsibilities (target architecture)

| Language | Responsibility | In this repo |
|---|---|---|
| TypeScript | Edge orchestration and execution | `skills/*/run.ts` contract; FastAPI app is the runtime today (see roadmap) |
| Python | AI skills, ONNX, Whisper, analytical processing | ✅ implemented and enforced |
| Go | Synchronization, replay, high-throughput services | target — `/api/v1/edge/outbox/drain` semantics implemented in Python |
| C++ | Native hardware interception and device integration | target — adapters are the integration seam |

## Monorepo target vs this repository

The specification's target layout is a full monorepo (`apps/edge-agent`,
`skills/*`, `services/sync-go`, …). This repository is a working FastAPI
monolith; the edge is implemented as a first-class module inside it
(`app/edge/` + `skills/` + `docs/`) rather than a second runtime. The mapping:

| Target | This repository |
|---|---|
| `apps/edge-agent/src/handlers/hardware-watcher.ts` | `app/api/edge.py` (`/ingest`) + `app/edge/adapters/*` |
| `apps/edge-agent/src/orchestration` | `app/edge/fabric.py` |
| `apps/edge-agent/src/event-router` | `ROUTES` in `app/edge/contracts.py` |
| `apps/edge-agent/src/ipc` | subprocess JSON contract (`app/edge/skills.py`) |
| `apps/edge-agent/src/continuity` | `app/edge/continuity.py`, `app/edge/recovery.py` |
| `apps/edge-agent/src/policy` | `app/edge/policy.py` (+ `edge_policy_rules`) |
| `apps/edge-agent/src/persistence` | `app/edge/models.py` |
| `packages/event-contracts` | `app/edge/contracts.py` |
| `packages/policy-engine` | `app/edge/policy.py` |
| `packages/audit-sdk` | `app/middleware/audit.py` + recommendation lifecycle |
| `skills/*` | ✅ `skills/` at repository root — exact target layout |
| `services/sync-go`, `services/replay-go`, `services/hardware-native/cpp` | not implemented — Python equivalents in `app/edge/` |
| `infrastructure/monorepo tooling` | standard `backend/` layout retained |

This is a deliberate, honest staging decision, not a silent divergence; the
remaining migration is mechanical and documented in the roadmap below.

## API map

| Method | Endpoint | Notes |
|---|---|---|
| GET | `/api/v1/edge/status` | continuity state + value + outbox depth + approved skills |
| GET | `/api/v1/edge/continuity/score` | computed + persisted value |
| POST | `/api/v1/edge/continuity/transition` | explicit transition; SAFE_MODE reachable from any state |
| GET | `/api/v1/edge/recovery/status` | recovery prerequisites, watermark and latest session |
| POST | `/api/v1/edge/recovery/connectivity-check` | step 1 — cloud reachability probe (or `local_only` mode) |
| POST | `/api/v1/edge/recovery/hash-verification` | step 2 — recompute payload hashes |
| POST | `/api/v1/edge/recovery/watermark-replay` | step 3 — contract replay + conflict detection |
| POST | `/api/v1/edge/recovery/cloud-ack` | step 4 — resolve, sync, advance watermark, go ONLINE |
| POST | `/api/v1/edge/recovery/reconcile` | convenience: run steps 1-4 in order |
| POST | `/api/v1/edge/devices/heartbeat` | device health (feeds continuity value) |
| GET | `/api/v1/edge/devices` | device health inventory |
| POST | `/api/v1/edge/ingest` | hardware/enterprise ingest → adapters → fabric |
| GET | `/api/v1/edge/events` | event store (filter route/status/event_type) |
| GET | `/api/v1/edge/events/{event_id}` | one event with authorization metadata |
| POST | `/api/v1/edge/events/{event_id}/reconcile` | count vs projection → recommendation (never auto-adjust) |
| GET | `/api/v1/edge/outbox` | queue depth + oldest pending |
| POST | `/api/v1/edge/outbox/drain` | honest dry-run unless `CLOUD_SYNC_URL` is set |
| GET | `/api/v1/edge/checkpoints` | replay watermarks |
| GET/POST | `/api/v1/edge/policy/rules` | policy database (create requires actor) |
| POST | `/api/v1/edge/policy/seed-defaults` | seed the five baseline rules |
| GET | `/api/v1/edge/skills` | approved skills + configs |
| POST | `/api/v1/edge/skills/{name}/run` | execute an approved skill (blocked in SAFE_MODE) |
| GET | `/api/v1/edge/event-types` | contract schema + known event types |

## Testing

`backend/tests/test_edge.py` covers: registry, policy seeding, HOLD-then-
validate flow for an unknown device, unrecognized quarantine, contract
validation (hash integrity), duplicate idempotency replay, the full
count-to-cycle-count reconciliation chain (ingest → variance → recommendation
→ approve → execute → ledger), skill execution, honest outbox dry-run,
continuity components, and the SAFE_MODE hard block with explicit recovery
back to ONLINE. `skills/*/tests/` contract-test every approved skill.

## Roadmap to the target architecture

1. Node edge runtime: `apps/edge-agent` implementing this exact contract (the
   API surface above is runtime-agnostic by design).
2. `services/sync-go`: outbox drain with backoff + watermark transactions;
   `/outbox/drain` semantics are the acceptance tests.
3. `services/hardware-native/cpp`: device interception behind the same
   adapter interfaces; adapters here are the reference behavior.
4. Real banner interface control documents replacing the demo contracts.
5. `CLOUD_SYNC_URL` + mTLS to the enterprise cloud, with the recovery flow as
   the reconciliation gate.
