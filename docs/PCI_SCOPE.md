# VE-JIT and PCI-DSS — Out of Scope by Design

**Scope verdict:** VE-JIT is **not in the cardholder-data environment (CDE)** and processes no cardholder data (CHD) or sensitive authentication data (SAD). It ingests non-PCI operational telemetry only — SKU-level sales facts (post-authorization), inventory on-hand and movements, lane telemetry, supplier performance and procurement data. This document defines the boundary, the enforcement, the compensating controls and the operator's obligations so retail IT/security can evidence the determination to a QSA or an internal PCI council.

> **One-line statement for reviewers:** VE-JIT never receives, stores, processes, transmits or logs PANs, track data, CVV/CVC/security codes, expiration dates, or cardholder names as payment context. Any such payload is rejected with HTTP 422 before pydantic ever validates it.

---

## 1. What enters VE-JIT — and what never does

| Data element | In scope for VE-JIT? | Why |
|---|---|---|
| SKU, quantity, line extended price (as line-item fact) | Yes | Post-authorization merchandise fact, no cardholder reference |
| Payment method label (`card` / `cash` / `ebt`) | Yes | A tender *type* is not cardholder data |
| Order total / receipt total | Yes | Aggregate fact, not card-derived |
| PAN (primary account number) | **Never** | Rejected at the API edge (422) |
| Card expiration, CVV/CVC/security code | **Never** | Rejected (422) |
| Track 1 / Track 2 data (magnetic stripe) | **Never** | Rejected (422) |
| Cardholder name as payment context | **Never** | Rejected (422) |

The sale facts VE-JIT consumes are the same ones the POS already publishes to its own merchandising feed; VE-JIT consumes a feed, not an interface to the cardholder data environment.

## 2. Where the boundary is enforced in code

| Enforcement point | Mechanism | Behavior |
|---|---|---|
| `app/pci_guard.py` | recursive detector: card-named keys + Luhn-13–19 digit runs + track-data regexes | raises sanitized `CardholderDataDetected` |
| `POST /api/v1/iot/normalize` | `forbid_cardholder_data_body` dependency | 422 **before** pydantic parsing |
| `POST /api/v1/edge/ingest` | same dependency | 422 before adapter translation |
| `POST /api/v1/commerce/pos/transactions`, `/orders`, `/ecommerce/orders` | same dependency | 422 |
| `POST /api/v1/cloud/recovery/sessions` | same dependency | 422 |
| Audit middleware (`app/middleware/audit.py`) | `pci_guard.redact` on the query string | logs never carry PANs |
| `GET /api/v1/compliance/pci-scope` | live out-of-scope posture artifact | machine-checkable declaration |

Every rejection's detail string names the **kind** of finding and the **JSON path**, never the digits. Tests assert the PAN never appears in response bodies (`backend/tests/test_pci_guard.py`).

## 3. Why a QSA can rely on this

Retail PCI assessors are right to be skeptical of a third-party binary next to a payment terminal. The compensating architecture is deliberately boring:

1. **Segmentation** — the POS/CDE VLAN and the store's back-office/management VLAN are separate; VE-JIT listens only on the back-office network. VE-JIT holds no route, credential, driver or device handle into the CDE.
2. **Data minimization** — the API accepts only the merchandising/operations vocabulary (SKU, qty, store, supplier, waste). Unknown fields carrying card data trip the Luhn detector and are rejected before any handler runs.
3. **Fail-closed gates** — deterministic policy gates, SAFE_MODE, Ed25519-signed policy bundles and the append-only hash-chained audit trail mean an attempted boundary violation produces a signed, reviewable event — not silent acceptance.
4. **Sandbox** — `vendarix-edge.service` and `vendarix-hw-adapter.service` run with `MemoryMax`, `NoNewPrivileges`, `ProtectSystem=strict`, `ProtectHome`, `PrivateTmp`, `AF_UNIX`-only address families, and (adapter) `0660` socket permissions via `vendarix-hw:vendarix-edge`.
5. **Evidence** — `pytest tests/test_pci_guard.py` is a standing regression suite asserting rejection paths, content sanitization and posture output; the audit log redaction is exercised by the same suite.

## 4. Operator obligations (what the store's IT/security team must do)

- Place VE-JIT's ports and sockets on the back-office/management VLAN only; do not route it to the POS/CDE VLAN.
- Do not mount card readers, PIN pads or payment pinpad serial/USB devices into the edge runtime.
- Keep `vendarix-hw-adapter.sock` and `edge.sock` mode `0660` with the `vendarix-edge` group only.
- Treat any 422-on-a-cardholder-data-alert as an incident-triggering signal: investigate the source feed, correct the integration, record in the incident runbook.
- Include VE-JIT's scope document (this file) and test output in the annual PCI scoping review.
- If any VE-JIT integration is ever extended to consume cardholder data, the CDE determination must be re-scoped — that is a change-management event, not a parameter toggle.

## 5. Evidence pack

| Evidence | Where |
|---|---|
| Scope declaration | this file |
| Enforcement code | `backend/app/pci_guard.py` |
| Middleware redaction | `backend/app/middleware/audit.py` |
| Regression tests | `backend/tests/test_pci_guard.py` |
| Posture artifact (machine-readable) | `GET /api/v1/compliance/pci-scope` |
| Sandbox units | `services/hardware_adapter/vendarix-hw-adapter.service`, `infrastructure/systemd/vendarix-edge.service` |
| Append-only audit | `infrastructure/database/append_only.sql` (+ `app/edge/integrity.py`) |
| Out-of-scope posture id | `vejit-pci-scope-v1` |

## 6. FAQ

**Q: Doesn't processing receipt totals constitute handling cardholder data?**
No. Aggregates are not cardholder data; VE-JIT never sees the authorization, the PAN or any SAD. Final tender type is recorded as a label, not a payment instrument.

**Q: Could a malicious POS feed inject a PAN into VE-JIT?**
It would be rejected at the API edge (422) with a sanitized error, logged via the redacting audit middleware, and surfaced to operators. Nothing would be persisted and the alert would fire on the feed's source — the integration contract itself forbids it, and the tests prove behavior.

**Q: What if we later want loyalty linkage at POS?**
Loyalty member linkage uses member numbers, not cardholder data; member number lookup flows through `/api/v1/commerce/loyalty` and is out of scope. Card-linked loyalty would require processing card data and re-scoping.
