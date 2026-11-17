# API Documentation

## Health
- `GET /health`
- `GET /health/live`
- `GET /health/ready`

## Inventory
- `GET /api/v1/inventory`
- `POST /api/v1/inventory`
- `PATCH /api/v1/inventory/{sku}`

## Queue
- `GET /api/v1/queue/recommendation`

## IoT
- `POST /api/v1/iot/normalize`

## Analytics
- `POST /api/v1/analytics/summary`

## JIT
- `GET /api/v1/jit/demo`
- `POST /api/v1/jit/emit-demo-event`

## Three planes
Full contracts and examples: `docs/THREE_PLANES.md`.
Planes: `docs/THREE_PLANES.md`. Edge: `docs/EDGE_ARCHITECTURE.md`.
- `GET /api/v1/planes` — plane registry and governance rule
- `GET /api/v1/planes/{plane_id}` — `commerce` | `operations` | `intelligence`

## Edge (Vendarix)
Full contracts and examples: `docs/EDGE_ARCHITECTURE.md`.
- `GET /api/v1/edge/status` — continuity state + value + outbox depth + approved skills
- `GET /api/v1/edge/continuity/score` · `POST /api/v1/edge/continuity/transition`
- `GET /api/v1/edge/recovery/status` · `POST /api/v1/edge/recovery/connectivity-check` · `POST /api/v1/edge/recovery/hash-verification`
- `POST /api/v1/edge/recovery/watermark-replay` · `POST /api/v1/edge/recovery/cloud-ack` · `POST /api/v1/edge/recovery/reconcile` (ordered steps; ONLINE only after a completed session)
- `POST /api/v1/edge/devices/heartbeat` · `GET /api/v1/edge/devices`
- `POST /api/v1/edge/ingest` — adapter → manifest → fabric → outbox
- `GET /api/v1/edge/events` · `GET /api/v1/edge/events/{event_id}` · `POST /api/v1/edge/events/{event_id}/reconcile`
- `GET /api/v1/edge/outbox` · `GET /api/v1/edge/outbox/deltas` · `POST /api/v1/edge/outbox/drain` (honest dry-run unless `CLOUD_SYNC_URL` is set)
- `GET /api/v1/edge/checkpoints`
- `GET /api/v1/edge/policy/rules` · `POST /api/v1/edge/policy/rules` · `POST /api/v1/edge/policy/seed-defaults`
- `GET /api/v1/edge/skills` · `POST /api/v1/edge/skills/{name}/run`
- `GET /api/v1/edge/event-types`
- `GET /api/v1/edge/policy/keys` · `POST /api/v1/edge/policy/keys` · `POST /api/v1/edge/policy/keys/{kid}/activate` · `POST /api/v1/edge/policy/keys/{kid}/retire`
- `GET /api/v1/edge/policy/bundle` · `POST /api/v1/edge/policy/bundle/install` (Ed25519 verification + monotonic version enforced; flips the hard safety gate to bundle verification)

## Cloud
- `POST /api/v1/cloud/policy/bundles/sign` (requires `CLOUD_SIGNER_TOKEN` + `POLICY_SIGNING_KEY`; header `x-cloud-signer-token`)
- `POST /api/v1/cloud/recovery/sessions` — hash-chain continuity, batch idempotency (dedup ledger), aggregate SALE apply with CAS, quarantine-first HITL routing
- `GET /api/v1/cloud/watermarks` · `GET /api/v1/cloud/quarantine` · `POST /api/v1/cloud/quarantine/{quarantine_id}/resolve` (`apply|discard`)
- `POST /api/v1/cloud/inventory/seed` · `GET /api/v1/cloud/inventory`

## Commerce
- `POST /api/v1/commerce/prices` · `GET /api/v1/commerce/prices` · `GET /api/v1/commerce/prices/evaluate`
- `POST /api/v1/commerce/promotions` · `GET /api/v1/commerce/promotions`
- `POST /api/v1/commerce/loyalty/members` · `GET /api/v1/commerce/loyalty/members/{member_number}`
- `POST /api/v1/commerce/orders` · `GET /api/v1/commerce/orders` · `GET /api/v1/commerce/orders/{order_ref}`
- `GET /api/v1/commerce/orders/{order_ref}/receipt` · `POST /api/v1/commerce/orders/{order_ref}/cancel`
- `POST /api/v1/commerce/pos/transactions` (idempotent POS entry point)
- `POST /api/v1/commerce/ecommerce/orders` · `GET /api/v1/commerce/pos/status`

## Operations
- `GET /api/v1/operations/ledger` · `POST /api/v1/operations/adjustments` · `POST /api/v1/operations/waste`
- `POST /api/v1/operations/suppliers` · `GET /api/v1/operations/suppliers` · `GET /api/v1/operations/suppliers/{id}`
- `POST /api/v1/operations/purchase-orders` · `GET /api/v1/operations/purchase-orders` · `GET /api/v1/operations/purchase-orders/{po_number}`
- `POST /api/v1/operations/purchase-orders/{po_number}/approve` (approver ≠ creator)
- `POST /api/v1/operations/purchase-orders/{po_number}/cancel`
- `POST /api/v1/operations/receiving` (receive against approved PO; restocks + ledger)
- `POST /api/v1/operations/transfers` · `GET /api/v1/operations/transfers` · `POST /api/v1/operations/transfers/{ref}/ship` · `POST /api/v1/operations/transfers/{ref}/receive`
- `POST /api/v1/operations/vendors/{supplier_id}/messages` · `GET /api/v1/operations/vendors/{supplier_id}/messages`
- `POST /api/v1/operations/recommendations/{uid}/execute` (executes APPROVED intelligence recommendations only)
- `GET /api/v1/operations/actions` (authorized-action audit ledger)

## Intelligence
- `POST /api/v1/intelligence/forecast/demand`
- `GET /api/v1/intelligence/anomalies` (`?create_recommendations=true` to persist PROPOSED recommendations)
- `GET /api/v1/intelligence/shelf-availability`
- `GET /api/v1/intelligence/workforce-guidance`
- `GET /api/v1/intelligence/suppliers/{supplier_id}/scorecard`
- `GET /api/v1/intelligence/waste/summary`
- `GET /api/v1/intelligence/recommendations` · `POST /api/v1/intelligence/recommendations`
- `GET /api/v1/intelligence/recommendations/{uid}` · `POST /api/v1/intelligence/recommendations/{uid}/approve` · `POST /api/v1/intelligence/recommendations/{uid}/reject`
- `GET /api/v1/intelligence/summary`
