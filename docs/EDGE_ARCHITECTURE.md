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

Every event carries tenant and store identity; source system and device
identity; a unique event id and idempotency key (unique constraint — replays
return the original event, `outcome=duplicate`); event type and schema
version; source sequence and timestamps; correlation and causation ids; a
payload hash (SHA-256 over canonical JSON); and processing and authorization
metadata. Validation failures are stored with status `REJECTED` and the
reasons — never silently dropped.

## Database and persistence architecture

Six logical domains (implemented; mapping shown so future migrations know
exactly what moves):

| Domain | Implementation |
|---|---|
| Event store | `edge_events` — immutable observed events (payload + hash) |
| Operational database | `inventory_items`, `purchase_orders`, `transfer_orders`, … (three planes) |
| Policy database | `edge_policy_rules` — named, versioned, activated/deactivated rules |
| Intelligence database | `recommendations` — forecasts, recommendations, model outputs |
| Continuity database | `edge_continuity_records`, `edge_continuity_scores`, `edge_checkpoints` |
| Audit ledger | `recommendations` lifecycle + `inventory_ledger` + `edge_events.authorization_metadata` + `audit_logs/audit.log` |

Pipeline layers, never collapsed:

```
OBSERVED EVENT → NORMALIZED EVENT → OPERATIONAL TRANSACTION → CURRENT PROJECTION
```

AI recommendations are not operational truth. A recommendation cannot mutate
inventory or authorize a purchase; only Operations (with an approved
recommendation where required) executes.

Implementation requirements — status:

| Requirement | Status |
|---|---|
| Transactional outbox | ✅ event + outbox row committed in one DB transaction |
| Unique constraints for idempotency | ✅ `edge_events.idempotency_key` unique |
| Append-only event history | ✅ `edge_events` rows are never updated in place (status column advances) |
| Optimistic concurrency | ⚠️ plane ledger uses signed deltas + `quantity >= 0` enforcement; no version columns yet |
| Store/tenant partitioning | ✅ `store_id` + `tenant_id` on every event and plane table; per-store deployment is the recommended model |
| Schema evolution | ✅ `schema_version` pinned (`vendarix.event.v1`); migrations via create_all + explicit migrations when a real DDL migration tool is adopted |
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
         connectivity checks → event verification → replay → conflict resolution → cloud ack
         → ONLINE   (or back to SAFE_MODE if any check fails)
```

Enforced in `app/edge/continuity.py`:

- SAFE_MODE is reachable from every state.
- OFFLINE/SAFE_MODE → ONLINE is **illegal**. Recovery is explicit:
  `transition.to_state=RECOVERING`, then `POST /api/v1/edge/recovery/reconcile`
  performs verification (hash re-check + contract re-validation of every
  pending outbox event), conflict resolution (`quarantine` | `accept_local`),
  and cloud acknowledgement (watermark advances, outbox acks, state → ONLINE).
- If verification fails, recovery stops in RECOVERING; ONLINE is never granted
  with unverified events.

SAFE_MODE blocks the business-action classes `inventory_move`,
`receiving_put_away`, `purchase_order`, `transfer_ship`, `transfer_receive`,
`waste_record`, `price_change`, `promotion_change`, `skill_execute` across
both the edge API and the operations plane (adjustments, receiving, transfers,
waste, PO approval, recommendation execution). Observation paths remain open.

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
| GET | `/api/v1/edge/recovery/status` | recovery prerequisites + watermark |
| POST | `/api/v1/edge/recovery/verify` | hash + contract verification (no state change) |
| POST | `/api/v1/edge/recovery/reconcile` | explicit recovery; requires RECOVERING |
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
