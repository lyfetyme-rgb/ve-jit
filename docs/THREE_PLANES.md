# Three Planes — Commerce, Operations, Intelligence

VE-JIT is organized into **three planes over one trusted data layer**. This
document is the contract for what each plane owns and how they interact. It
mirrors the platform specification's "The three planes and their
responsibilities" section; the Edge architecture section is intentionally
absent and will be added when its specification is finalized.

## The planes

| Plane | Owns | Primary responsibility |
|---|---|---|
| **Commerce** | Customer transactions | Customer-facing commerce activity |
| **Operations** | Physical business processes | Inventory, purchasing, receiving, replenishment |
| **Intelligence** | Analytical decisions | Forecasting, recommendations, anomaly detection |

### Commerce plane

- POS integration
- Orders and transactions
- Pricing and promotions
- Loyalty
- eCommerce
- Customer-facing workflows

### Operations plane

- Inventory ledger
- Purchasing and procurement
- Replenishment
- Receiving and put-away
- Distribution-center transfers
- Supplier terms and SLAs
- Vendor collaboration

### Intelligence plane

- Demand forecasting
- Inventory anomaly detection
- Shelf availability insights
- Queue and workforce guidance
- Supplier intelligence
- Waste and spoilage analysis

## The governing rule (non-negotiable)

> **Intelligence produces recommendations.**
> **Operations executes authorized business actions.**
> **Commerce remains responsible for the customer-facing transaction.**

How the codebase enforces it:

- `app/planes/intelligence.py` contains only computations and reads, plus the
  ability to persist `PROPOSED` rows in the `recommendations` table. It never
  writes stock, prices, orders, purchase orders or transfers.
- `app/planes/operations.py` (and the routers) execute business actions.
  Every stock change flows through `apply_stock_movement()`, which updates the
  cached `inventory_items.quantity` and appends an `inventory_ledger` row.
  Recommendation-driven actions additionally pass through
  `app/planes/governance.py`, which refuses to execute anything that is not
  `APPROVED` (HTTP 409), and records who approved and who executed.
- `app/planes/commerce.py` owns the customer-facing transaction end to end:
  pricing, promotions, loyalty, orders, receipts and cancellation. POS and
  eCommerce are two entry points on the same transaction rail
  (`/api/v1/commerce/pos/transactions`, `/api/v1/commerce/ecommerce/orders`).

## Recommendation lifecycle

```
PROPOSED --approve(approver)--> APPROVED --execute(actor)--> EXECUTED
    |                                                       (Operations only;
    +--reject(actor)-----------> REJECTED                    execution_ref recorded)
```

- There is no path from `PROPOSED` to `EXECUTED` in a single call.
- A recommendation is not authority. Operations refuses to execute a
  recommendation unless it is `APPROVED`.
- Execution is idempotent at the action level: executing an already-executed
  recommendation returns 409; the action it created is referenced by
  `execution_ref` (e.g. a `PO-…` purchase order number).
- Approval details are persisted on the recommendation row (`approved_by`,
  `approved_at`, `executed_by`, `executed_at`) and mirrored by the audit
  middleware (`audit_logs/audit.log`) for every request.

## API map

### Planes

| Method | Endpoint | Notes |
|---|---|---|
| GET | `/api/v1/planes` | All three planes: owns, responsibilities, capabilities, governance rule |
| GET | `/api/v1/planes/{plane_id}` | `commerce` \| `operations` \| `intelligence` |

### Commerce

| Method | Endpoint | Notes |
|---|---|---|
| POST | `/api/v1/commerce/prices` | Create price book entry (store-specific or chain-wide) |
| GET | `/api/v1/commerce/prices` | List active prices |
| GET | `/api/v1/commerce/prices/evaluate` | Price + best promotion for a SKU |
| POST | `/api/v1/commerce/promotions` | Create promotion (`percent_off` / `amount_off`) |
| GET | `/api/v1/commerce/promotions` | List promotions |
| POST | `/api/v1/commerce/loyalty/members` | Enroll loyalty member |
| GET | `/api/v1/commerce/loyalty/members/{member_number}` | Balance and tier |
| POST | `/api/v1/commerce/orders` | Place + settle order (channel: pos/ecommerce/mobile/kiosk) |
| POST | `/api/v1/commerce/pos/transactions` | POS entry point; channel forced to `pos`; idempotent on `client_order_id` |
| POST | `/api/v1/commerce/ecommerce/orders` | eCommerce entry point; channel forced to `ecommerce` |
| GET | `/api/v1/commerce/pos/status` | POS integration contract |
| GET | `/api/v1/commerce/orders` | Order history (filter by channel/status) |
| GET | `/api/v1/commerce/orders/{order_ref}` | Order detail |
| GET | `/api/v1/commerce/orders/{order_ref}/receipt` | Customer-facing receipt |
| POST | `/api/v1/commerce/orders/{order_ref}/cancel` | Cancel: restocks lines, claws back loyalty points |

Pricing rule: the server is the only authority on basket cost. Base price
comes from the active price book (store-specific, else chain-wide); the
single best active promotion applies per line. A client-supplied `unit_price`
is honoured only when no price book entry exists — never as an override.

### Operations

| Method | Endpoint | Notes |
|---|---|---|
| GET | `/api/v1/operations/ledger` | Append-only stock movement journal (filter by sku/reason) |
| POST | `/api/v1/operations/adjustments` | Signed stock adjustment (`ADJUSTMENT` / `CYCLE_COUNT`), requires actor |
| POST | `/api/v1/operations/waste` | Record waste event (SPOILAGE/EXPIRED/DAMAGE/SHRINK/OTHER); posts WASTE ledger entry |
| POST | `/api/v1/operations/suppliers` | Create supplier with terms/SLAs (payment terms, lead time, OTIF %, fill-rate %) |
| GET | `/api/v1/operations/suppliers` | List suppliers |
| GET | `/api/v1/operations/suppliers/{id}` | Supplier detail |
| POST | `/api/v1/operations/purchase-orders` | Create DRAFT purchase order |
| GET | `/api/v1/operations/purchase-orders` | List POs (filter by status) |
| GET | `/api/v1/operations/purchase-orders/{po_number}` | PO detail with lines |
| POST | `/api/v1/operations/purchase-orders/{po_number}/approve` | `DRAFT` → `APPROVED`; approver ≠ creator (403) |
| POST | `/api/v1/operations/purchase-orders/{po_number}/cancel` | Cancel before receipt |
| POST | `/api/v1/operations/receiving` | Receive against an APPROVED PO; restocks, posts RECEIPT ledger, rolls PO to PARTIALLY_RECEIVED/RECEIVED |
| POST | `/api/v1/operations/transfers` | Create DC transfer (`REQUESTED`) |
| GET | `/api/v1/operations/transfers` | List transfers |
| POST | `/api/v1/operations/transfers/{ref}/ship` | `REQUESTED` → `SHIPPED`; TRANSFER_OUT ledger |
| POST | `/api/v1/operations/transfers/{ref}/receive` | `SHIPPED` → `RECEIVED`; TRANSFER_IN ledger |
| POST | `/api/v1/operations/vendors/{supplier_id}/messages` | Vendor collaboration message (`TO_VENDOR`/`FROM_VENDOR`) |
| GET | `/api/v1/operations/vendors/{supplier_id}/messages` | Message history per supplier |
| POST | `/api/v1/operations/recommendations/{uid}/execute` | Execute an APPROVED Intelligence recommendation (see below) |
| GET | `/api/v1/operations/actions` | Audit view: every recommendation that was executed, with executor and ref |

### Intelligence

| Method | Endpoint | Notes |
|---|---|---|
| POST | `/api/v1/intelligence/forecast/demand` | SES/SMA forecast from supplied `history` or trailing 28-day sales for `sku` |
| GET | `/api/v1/intelligence/anomalies` | Stockout / stockout-risk / overstock dead-stock scan; `create_recommendations=true` persists PROPOSED replenishment recommendations |
| GET | `/api/v1/intelligence/shelf-availability` | Days-of-supply and availability band per SKU |
| GET | `/api/v1/intelligence/workforce-guidance` | Queue/workforce guidance wrapped as a PROPOSED recommendation |
| GET | `/api/v1/intelligence/suppliers/{id}/scorecard` | Fill rate + on-time vs supplier SLA, from recorded POs and receipts |
| GET | `/api/v1/intelligence/waste/summary` | Waste units/cost by reason and SKU + dead-stock carrying watch |
| GET | `/api/v1/intelligence/recommendations` | List/filter recommendations |
| POST | `/api/v1/intelligence/recommendations` | Create a manual recommendation (analysis output) |
| GET | `/api/v1/intelligence/recommendations/{uid}` | Recommendation detail |
| POST | `/api/v1/intelligence/recommendations/{uid}/approve` | Human approval gate |
| POST | `/api/v1/intelligence/recommendations/{uid}/reject` | Human rejection |
| GET | `/api/v1/intelligence/summary` | Counts by status + anomaly count + 30-day waste cost |

`POST /api/v1/operations/recommendations/{uid}/execute` semantics:

- `REPLENISHMENT` → creates a purchase order in `APPROVED` status (authorized
  by the recommendation's approver). Payload: `{supplier_id, lines:[…]}` or
  the minimal `{sku, suggested_order_quantity}` produced by the anomaly
  scanner, which is routed to the lowest-id active supplier.
- `TRANSFER` → creates a `REQUESTED` transfer from
  `{from_store, to_store, sku, quantity}`.
- Any other kind → acknowledged with no side effects (`execution_ref` records
  `ack:<uid>`).

## Data model

New tables (created by the same `Base.metadata.create_all` at startup; the
baseline `inventory_items` table is untouched):

- Commerce: `product_prices`, `promotions`, `loyalty_members`,
  `commerce_orders`, `commerce_order_lines`
- Operations: `inventory_ledger`, `suppliers`, `purchase_orders`,
  `purchase_order_lines`, `transfer_orders`, `vendor_messages`, `waste_records`
- Intelligence: `recommendations`

`inventory_ledger` is append-only. `increments`/`decrements` are signed
integer deltas with a reason, a reference (order ref, PO number, transfer ref,
waste id) and the acting identity. The cached quantity is for reads; the
ledger is the audit trail.

## Identity and authorization

Actor identity is passed explicitly (`actor`, `approver`, `created_by`,
`register_id`) and persisted on every mutation, mirroring the rest of the
codebase (audit middleware + explicit actor fields, no anonymous paths). POS
requests are tied to a `register_id`; cancellations require `actor` +
`reason`; approvals require a named human and enforce creator ≠ approver.

For a live chain rollout, bind these actor fields to the SSO/OIDC claims at
the edge (the app issues its own JWTs under `/api/v1/auth` for service
identities). The three-plane split does not change the security model; it
constrains *which plane may take which kind of action*.

## Customer-facing workflow walkthrough

1. Shopper at lane 7 scans a gallon of milk. POS posts to
   `/api/v1/commerce/pos/transactions` with `register_id=lane-7`,
   `client_order_id=<terminal idempotency key>`, and the basket lines.
2. Commerce resolves the price book, applies the best active promotion, checks
   stock, settles the order as `PAID`, accrues loyalty points and writes
   `SALE` ledger entries. If stock is insufficient the whole order fails with
   409 and nothing is written.
3. The receipt is available at `/api/v1/commerce/orders/{ref}/receipt`; a
   cancellation at `/cancel` restocks and claws back points.
4. Intelligence sees velocity drop on dairy SKUs via
   `/api/v1/intelligence/anomalies`, emits `REPLENISHMENT` recommendations —
   it *cannot* raise a PO.
5. A human approves the recommendation; Operations executes it, producing an
   approved PO routed to the right supplier, and the whole chain is auditable
   via `/api/v1/operations/actions`.

## Testing

`backend/tests/test_planes.py` covers: plane registry and governance rule,
POS ordering with price book + promotion + loyalty + idempotency, price-book
precedence, order cancellation restock, PO segregation of duties (creator ≠
approver), receiving rules, transfers, waste ledger postings, the full
recommendation lifecycle (execute-before-approve → 409), replenishment
execution into an approved PO, forecasting, anomaly scanning, shelf
availability, supplier scorecards and workforce guidance. Tests run against an
isolated SQLite database via a `get_db` dependency override.

## Known limits / roadmap

- Money is stored as `Float` (consistent with the existing `unit_cost`
  pattern). A decimal migration would be appropriate before financial
  reporting goes live.
- POS integration is `direct-post` over HTTPS on the trusted network; a
  device-gateway mode with signed terminal certificates is a follow-up.
- `inventory_items` is single-location. Multi-store visibility requires
  adding `location_id` to the item table (breaking migration); transfers are
  modelled per direction on a per-node basis until then.
- Recommendation expiry is modelled but not enforced by a scheduler.
- The Edge architecture (platform specification section 3) is **not** covered
  by this document or the implementation; it will be added when specified.
