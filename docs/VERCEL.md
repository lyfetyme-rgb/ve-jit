# Vercel Deployment — Static Dashboard Only

VE-JIT on Vercel means **one thing only**: the static UIs in `frontend/` as a
site. The Python backend, the store edge runtime, and the C++/Go sidecars are
**not** deployed to Vercel (they need a long-running process, local UDS sockets,
systemd and a persistent database).

## Deploy

1. Import `lyfetyme-rgb/ve-jit` into Vercel as a **static project**.
   `vercel.json` sets `outputDirectory: frontend`, `framework: null`.
2. The dashboard loads `config.js` before `app.js`. Replace `frontend/config.js`
   with the backend origin (or inject it in your build step):

   ```js
   window.VEJIT_API_BASE = "https://vejit-api.example.com";
   ```

   With the default empty string, API calls use same-origin paths — only useful
   when the dashboard is served by FastAPI itself at `/ui/`.
3. On the backend, allow the Vercel domain:

   ```
   ALLOWED_ORIGINS=https://your-app.vercel.app
   ```

4. All API calls now require a bearer token. The static UI presents a sign-in
   dialog and keeps the token in `sessionStorage`; there is no token in the
   static bundle. The backend must be reachable over HTTPS.

## What works / what does not

| Feature | On Vercel |
|---|---|
| Health, planes, edge status, PCI posture, BENGAL capability metadata | Yes (reads from backend API base) |
| Store Console tenant switching, inventory, governed approve/execute | Yes, with a valid token and CORS configured |
| Atlassian DLQ banner, JSM incident list | Yes (reads through central backend) |
| Writes to the edge database / policy gates | No — those stay on the central backend |
| Store edge runtime, UDS hardware adapter, sync-go worker | No — must run on store hardware via the released bundles |
| Cloud functions / database on Vercel | Not in this change; the Python service remains on its own host |

## Security notes

- `config.js` is public: put only an origin URL there, never a token or secret.
- `vercel.json` sets `no-cache` on `config.js` and `nosniff` on all paths.
- `tests/test_vercel_static.py` checks the wiring and scans the frontend bundle
  for secret-looking strings; it runs in CI.
- See `docs/IDENTITY_AUTHORIZATION.md` for OIDC/local token setup.

## Summary

Vercel hosts the **window into VE-JIT**, not VE-JIT itself. Store execution,
governance and audit remain where they are — on the edge node and the central
backend — which is the same boundary the whole architecture is built around.
