"""Payment authorization boundary.

VE-JIT never handles cardholder data. Callers pass an **opaque PSP token**;
the adapter forwards only that token to the payment provider. The PCI guard
on the API route rejects anything that looks like a PAN before parsing, so a
card number placed in ``payment_token`` is refused.

Two adapters:

- ``none``: local demo/no-op, returns a deterministic authorization ref.
- ``tokenized``: calls the configured PSP with a bearer token from config
  (or ``PAYMENT_PSP_TOKEN_FILE``). Works with any provider that accepts the
  documented JSON shape; a real integration implements the same interface.
"""

import hashlib
from typing import Protocol

import httpx

from app.config import settings


class PaymentError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class PaymentAdapter(Protocol):
    def authorize(
        self, *, reference: str, amount: float, store_id: str, currency: str = "USD"
    ) -> str:
        """Return an authorization reference. Must never receive a PAN."""


class NoopPaymentAdapter:
    def authorize(
        self, *, reference: str, amount: float, store_id: str, currency: str = "USD"
    ) -> str:
        digest = hashlib.sha256(f"{reference}:{amount}:{store_id}".encode()).hexdigest()[:16]
        return f"demo-auth-{digest}"


class TokenizedPaymentAdapter:
    def __init__(self, url: str, token: str):
        self.url = url
        self.token = token

    def authorize(
        self, *, reference: str, amount: float, store_id: str, currency: str = "USD"
    ) -> str:
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {self.token}"}
        payload = {
            "reference": reference,
            "amount": round(float(amount), 2),
            "currency": currency,
            "store_id": store_id,
            "token": reference,  # opaque PSP token supplied by the client
        }
        try:
            response = httpx.post(self.url, json=payload, headers=headers, timeout=10.0)
        except httpx.HTTPError as exc:
            raise PaymentError(503, f"Payment provider unavailable: {exc}") from exc
        if response.status_code >= 400:
            raise PaymentError(502, "Payment authorization was declined or failed")
        body = response.json()
        auth_ref = body.get("authorization_id") or body.get("id")
        if not auth_ref:
            raise PaymentError(502, "Payment provider returned no authorization reference")
        return str(auth_ref)


def current_adapter() -> PaymentAdapter:
    mode = (settings.PAYMENT_ADAPTER or "none").strip().lower()
    if mode == "tokenized":
        if not settings.PAYMENT_PSP_URL or not settings.PAYMENT_PSP_TOKEN:
            raise PaymentError(503, "Tokenized payments are not configured")
        return TokenizedPaymentAdapter(settings.PAYMENT_PSP_URL, settings.PAYMENT_PSP_TOKEN)
    if mode == "none":
        return NoopPaymentAdapter()
    raise PaymentError(503, f"Unsupported PAYMENT_ADAPTER {mode!r}")
