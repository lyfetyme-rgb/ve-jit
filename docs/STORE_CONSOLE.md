# VE-JIT Store Console

A working multi-tenant retail application built on the governed VE-JIT core.
**No retailer names are hardcoded.** Companies are loaded from a JSON catalog,
so the same code can serve any grocery, hardware, home-improvement or other
retail operation.

## Shipped demo archetypes

`config/retailers.demo.json` contains three generic examples:

| Tenant id | Name | Vertical | Store | Arch | SKU namespace |
|---|---|---|---|---|---|
| `grocery_demo` | Demo Grocery Co | grocery | `GR-VA-042` | amd64 | `GR-*` |
| `hardware_demo` | Demo Hardware Co-op | hardware | `HW-NC-001` | arm64 | `HW-*` |
| `home_improvement_demo` | Demo Home Improvement Co | home_improvement | `HI-NC-DC1` | amd64 | `HI-*` |

These are intentionally generic archetypes, not real chains.

## Run it

```bash
cd backend
./.venv-win/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open:

- **Store Console:** http://127.0.0.1:8000/console/
- **Platform dashboard:** http://127.0.0.1:8000/ui/
- **API docs:** http://127.0.0.1:8000/docs

The catalog is loaded idempotently on startup (and in test fixtures), so
restarting never duplicates data.

## Load your own companies

Copy the demo catalog and edit it:

```bash
cp config/retailers.demo.json config/my-retailers.json
```

Each retailer entry:

```json
{
  "tenant_id": "my_customer",
  "name": "My Customer",
  "vertical": "grocery",
  "description": "Any descriptive text",
  "sku_prefix": "MC-",
  "stores": [
    { "store_id": "MC-001", "name": "Main Store", "location": "Anywhere, US",
      "architecture": "amd64", "channel": "store" }
  ],
  "catalog": [
    { "sku": "MC-100", "name": "Example Item", "price": 4.99,
      "quantity": 40, "reorder_point": 10 }
  ]
}
```

Then either set the environment variable before starting the backend:

```
RETAILER_CONFIG=/absolute/path/to/my-retailers.json
```

or load it explicitly against any database:

```bash
python tools/load_retailers.py config/my-retailers.json --db sqlite:///./vejit.db
```

Adding a company is a **data change, not a code change**. Multiple retailers,
multiple stores per retailer, and arbitrary verticals are supported. The
loader validates required fields before writing anything.

`sku_prefix` scopes inventory reads per tenant; it is stored on the tenant
record, so no code map needs editing.

## What a store can actually do

| Action | Route | Governance |
|---|---|---|
| Switch tenant/store | UI dropdowns | Every route validates the tenant→store relationship and the token scopes; cross-tenant reads return 403/404 |
| View KPIs + posture | `GET /api/v1/console/overview` | Reads only; includes PCI scope and BENGAL metadata |
| View stock | `GET /api/v1/console/inventory` | Scoped to the tenant's SKU namespace |
| Post a POS sale | `POST /api/v1/console/orders` | Commerce `place_order`; cardholder data rejected with 422 before parsing |
| Approve a recommendation | `POST /api/v1/console/recommendations/{uid}/approve` | Actor bound to token; role `store_manager`+ required |
| Execute a recommendation | `POST /api/v1/console/recommendations/{uid}/execute` | Reuses Operations executor; hard safety gates fail closed; 409 before approval |
| View execution ledger | `GET /api/v1/console/audit` | Append-only inventory ledger + authorized action trail |

All routes require a bearer token — see `docs/IDENTITY_AUTHORIZATION.md`.

## Three-plane boundary (unchanged)

- **Commerce** owns the customer-facing transaction (POS sale).
- **Intelligence** proposes recommendations; it never executes them.
- **Operations** executes only after a named human approves, and only through
  the existing governed executor — the console adds no side-channel.

The console is a thin surface over the same domain services, outbox and
append-only ledgers used everywhere else in VE-JIT. There is no separate
console database, no console-only policy bypass, and no credentials in the
static bundle.

## Tests

```bash
cd backend
./.venv-win/Scripts/python.exe -m pytest tests/test_store_console.py -q
```

Covers: three verticals present, idempotent load, tenant-scoped reads,
cross-tenant rejection, UI mount, full propose→approve→execute→audit flow
including the pre-approval 409 gate, and PCI rejection of card data.

## Static / Vercel hosting

`frontend/console/` is plain HTML/CSS/JS like the main dashboard. It reads
`config.js` (`window.VEJIT_API_BASE`) before `app.js`; for static hosting set
that to the central backend origin. It does not run edge nodes or sidecars —
see `docs/VERCEL.md` for the broader boundary.
