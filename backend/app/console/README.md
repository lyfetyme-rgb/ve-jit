# Store Console domain package

Config-driven retailer registry (`models.py`), loader (`seed.py`) for tenants,
stores, SKUs, prices and governed recommendations, and the SKU-namespace lookup
used by the console API. No retailer names are hardcoded.

Default catalog: `config/retailers.demo.json` (generic grocery, hardware and
home-improvement archetypes). Override with `RETAILER_CONFIG=/path/to/file.json`.

The HTTP surface lives in `app/api/console.py` and the UI in
`frontend/console/`. Full documentation: `docs/STORE_CONSOLE.md`.
