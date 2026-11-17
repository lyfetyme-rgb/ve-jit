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

## Planes
Full contracts and examples: `docs/THREE_PLANES.md`.
- `GET /api/v1/planes` — plane registry and governance rule
- `GET /api/v1/planes/{plane_id}` — `commerce` | `operations` | `intelligence`

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
