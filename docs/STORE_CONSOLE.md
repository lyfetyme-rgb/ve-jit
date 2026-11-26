# VE-JIT Store Console

A working multi-tenant retail application built on the governed VE-JIT core.
It ships with three demo retail customers:

| Tenant | Vertical | Demo store | Edge arch | SKU namespace |
|---|---|---|---|---|
| Food Lion | Grocery / cold chain | `FL-VA-042` (Richmond, VA) | amd64 | `FL-*` |
| Ace Hardware | Hardware co-op | `ACE-NC-001` (Raleigh, NC) | arm64 | `ACE-*` |
| Lowe's | Home improvement / DC | `LOW-NC-DC1` (Charlotte, NC) | amd64 | `LOW-*` |

## Run it

```bash
cd backend
./.venv-win/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open:

- **Store Console:** http://127.0.0.1:8000/console/
- **Platform dashboard:** http://127.0.0.1:8000/ui/
- **API docs:** http://127.0.0.1:8000/docs

Demo tenants, stores, SKUs, an order and a recommendation are seeded
idempotently on startup (and on every test fixture), so restarting never
duplicates data.

## What a store can actually do

| Action | Route | Governance |
|---|---|---|
| Switch tenant/store | UI dropdowns | Every route validates the tenant→store relationship; cross-tenant reads return 404 |
| View KPIs + posture | `GET /api/v1/console/overview` | Reads only; includes PCI scope and BENGAL metadata |
| View stock | `GET /api/v1/console/inventory` | Scoped to tenant SKU namespace |
| Post a POS sale | `POST /api/v1/console/orders` | Commerce `place_order`; cardholder data rejected with 422 before parsing |
| Approve a recommendation | `POST /api/v1/console/recommendations/{uid}/approve` | Human named in `actor`; cross-tenant approval refused |
| Execute a recommendation | `POST /api/v1/console/recommendations/{uid}/execute` | Reuses the Operations executor; hard safety gates enforced (fail-closed); PROPOSED → 409 |
| View execution ledger | `GET /api/v1/console/audit` | Append-only inventory ledger + authorized action trail |

## Three-plane boundary (unchanged)

- **Commerce** owns the customer-facing transaction (POS sale).
- **Intelligence** proposes recommendations; it never executes them.
- **Operations** executes only after a named human approves, and only through
  the existing governed executor — the console adds no side-channel.

The console is a thin surface over the same domain services, outbox and
append-only ledgers used everywhere else in VE-JIT. There is no separate
console database, no console-only policy bypass, and no credentials in the
static bundle.

## Multi-tenancy notes

Tenant scoping is enforced in `_store()` on every data route. The demo tenants
are seeded rows in `console_tenants` / `console_stores`; adding a real retailer
is a data change, not a code change. SKU namespaces are explicit in
`app/console/seed.py::TENANT_SKU_PREFIXES` so short codes stay stable.

`Recommendation` gained nullable `tenant_id` / `store_id` columns for scoping;
older rows remain valid (nullable), and `ensure_version_columns()` applies the
additive migration to existing SQLite databases at startup.

## Tests

```bash
cd backend
./.venv-win/Scripts/python.exe -m pytest tests/test_store_console.py -q
```

Covers: three verticals present, idempotent seed, tenant-scoped reads,
cross-tenant rejection, UI mount, full propose→approve→execute→audit flow
including the pre-approval 409 gate, and PCI rejection of card data.

## Static / Vercel hosting

`frontend/console/` is plain HTML/CSS/JS like the main dashboard. It reads
`config.js` (`window.VEJIT_API_BASE`) before `app.js`; for static hosting set
that to the central backend origin. It does not run edge nodes or sidecars —
see `docs/VERCEL.md` for the broader boundary.
