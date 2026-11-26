# Vercel Deployment — Static Dashboard Only

VE-JIT on Vercel means **one thing only**: the read-only dashboard in
`frontend/` as a static site. The Python backend, the store edge runtime, and
the C++/Go sidecars are **not** deployed to Vercel (they need a long-running
process, local UDS sockets, systemd and a persistent SQLite database).

## Deploy

1. Import `lyfetyme-rgb/ve-jit` into Vercel as a **static project**.
   `vercel.json` sets `outputDirectory: frontend`, `framework: null`.
2. The dashboard loads `config.js` before `app.js`. In your Vercel project,
   replace `frontend/config.js` with the backend origin (or add a build step):

   ```js
   window.VEJIT_API_BASE = "https://vejit-api.example.com";
   ```

   With the default empty string, API calls use same-origin paths — only useful
   when the dashboard is served by FastAPI itself at `/ui/`.
3. On the backend, allow the Vercel domain:

   ```
   ALLOWED_ORIGINS=https://your-app.vercel.app
   ```

   TLS is handled by the backend ingress; the static site never holds tokens.

## What works / what does not

| Feature | On Vercel |
|---|---|
| Health, planes, edge status, PCI posture, BENGAL capability metadata | Yes (reads from backend API base) |
| Buttons that POST (create demo SKU, IoT normalize) | Yes, if CORS allows the Vercel origin |
| Atlassian DLQ banner, JSM incident list | Yes (reads through central backend) |
| Writes to the edge database / policy gates | No — those stay on the central backend, not the static site |
| Store edge runtime, UDS hardware adapter, sync-go worker | No — must run on store hardware via the released `.deb`/`.tar.gz` bundles |
| Cloud functions / database on Vercel | Not in this change; the Python service remains on its own host |

## Security notes

- `config.js` is public: put only an origin URL there, never a token or secret.
- `vercel.json` sets `Cache-Control: no-cache` on `config.js` so origin changes
  propagate immediately, and `X-Content-Type-Options: nosniff` on all paths.
- `tests/test_vercel_static.py` checks the wiring and scans the frontend bundle
  for secret-looking strings; it runs in CI.

## Summary

Vercel hosts the **window into VE-JIT**, not VE-JIT itself. Store execution,
governance and audit remain where they are — on the edge node and the central
backend — which is the same boundary the whole architecture is built around.
