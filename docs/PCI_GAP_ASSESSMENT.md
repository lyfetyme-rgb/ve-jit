# PCI DSS Scope — Gap Assessment (not a certification)

This document is an **engineering gap list**, not a PCI DSS assessment or
attestation. Only a qualified assessor (QSA) can determine scope and compliance.

## Design boundary (implemented)

- VE-JIT accepts no cardholder data. `app/pci_guard.py` rejects Luhn-valid PANs
  and card-named fields with HTTP 422 **before pydantic parsing**.
- Redacting audit middleware prevents card data from entering logs.
- `GET /api/v1/compliance/pci-scope` exposes the declared posture as data.
- Payments are handled by an adapter that accepts only an **opaque PSP token**
  (`docs/STORE_CONSOLE.md`, `app/integrations/payments.py`). A PAN placed in the
  token field is rejected by the guard.
- The store console never renders or stores a card number.

## Gaps that must be closed by a QSA / your payment team

| Area | Status |
|---|---|
| Formal scope determination (SAQ type / ROC) | **Not done** — requires QSA |
| PSP integration contract and tokenization flow | Adapter boundary exists; provider integration not configured |
| Network segmentation evidence (CDE vs non-CDE) | Not assessed |
| Quarterly vulnerability scanning / ASV | Not done |
| Penetration test | Not done |
| Key management and rotation evidence | Interface exists; HSM/KMS not configured |
| Access control reviews and least-privilege proof | RBAC exists; no formal review |
| Log retention and monitoring policy | Runbook exists; retention not enforced |
| Incident response plan sign-off | Runbook draft only |
| Merchant agreements with each retailer | External, not in code |

## What this means

The software boundary is intentionally conservative — it is designed to keep
VE-JIT **out of scope** by never receiving cardholder data. That design must be
verified against your actual payment flow, network and contracts before any
retailer deployment. Do not represent this project as PCI compliant on the
basis of these files.
