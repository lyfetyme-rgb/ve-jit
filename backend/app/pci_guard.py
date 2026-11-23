"""PCI-DSS scope boundary for VE-JIT.

VE-JIT is **out of scope for the cardholder-data environment (CDE)** by
design: it ingests non-PCI SKU / inventory / lane telemetry only and must
never receive, persist, log or forward cardholder data (PAN, track data,
CVV/CVC, expiration, cardholder name as payment context).

This module enforces that boundary at the trust edge:

1. `contains_cardholder_data(obj)` — recursive detector (key-name heuristics +
   Luhn-validated digit runs).
2. `enforce_no_cardholder_data(...)` — FastAPI dependency factory: any
   request whose JSON body trips the detector is rejected with HTTP 422 and a
   sanitized reason that never echoes the offending digits.
3. `redact(value)` — replaces PAN-class digit runs and card-named fields with
   `[REDACTED-PAN]`; used by the audit middleware so stray cardholder data
   can never reach the on-disk log.

Design rules:
- The detector is deliberately strict (false positives possible, never false
  negatives worth having): a Luhn-passing 13–19 digit run anywhere in a
  payload is treated as a PAN.
- The exception / 422 message names the *kind* of finding and the JSON path —
  NEVER the card data itself.
- Nothing in this module converts, hashes or tokenizes cardholder data.
  Retail operators keep PANs inside the POS lane's P2PE/HSM scope; VE-JIT
  only ever sees the post-authorization, non-financial SKU fact stream.
"""

import json
import re
from typing import Any

from fastapi import HTTPException, Request
from json import JSONDecodeError

PAN_DIGIT_RUN = re.compile(r"(?<![0-9A-Za-z])(\d[\d\s\-]{10,19}\d)(?![0-9A-Za-z])")
TRACK_DATA = re.compile(r"%B[0-9]{13,19}\^|;[0-9]{13,19}=|\?\d?[0-9]{2,}")
KEY_NAMES = re.compile(
    r"(?i)(pan|card[_\s\-]?(?:number|no|num)|cardnumber|cc[_\s\-]?number|primary[_\s\-]?account(_number)?|"
    r"credit[_\s\-]?card[_\s\-]?(?:number|no)?|full[_\s\-]?track|track[_\s\-]?[12]|cvv|cvc|cvn|cid|"
    r"card[_\s\-]?security(_code)?|expiration|expiry|exp[_\s\-]?(?:date|month|year)|cardholder[_\s\-]?(?:name|number))"
)


class CardholderDataDetected(Exception):
    """Raised when a payload appears to carry cardholder data. Message is sanitized."""

    def __init__(self, path: str, kind: str):
        super().__init__(f"cardholder data detected at {path} ({kind}); VE-JIT must not receive payment card data")
        self.path = path
        self.kind = kind


def _digits(value: str) -> str:
    return "".join(ch for ch in value if ch.isdigit())


def luhn_ok(number: str) -> bool:
    digits = [int(c) for c in number if c.isdigit()]
    if len(digits) < 13 or len(digits) > 19:
        return False
    checksum = 0
    parity = len(digits) % 2
    for i, d in enumerate(digits):
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        checksum += d
    return checksum % 10 == 0


def _contains_pan_in_text(text: str) -> bool:
    # Track data is unambiguous cardholder data wherever it appears.
    if TRACK_DATA.search(text):
        return True
    # Reject a digit run only when it is not embedded in a longer alphanumeric
    # token (IDs, hashes, correlation ids are hex and can contain Luhn-like runs).
    for match in PAN_DIGIT_RUN.finditer(text):
        if luhn_ok(_digits(match.group(1))):
            return True
    return False


def _scan(value: Any, path: str) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            lowered = str(key).lower()
            if KEY_NAMES.search(lowered):
                # Any value present under a card-named key is presumed cardholder data.
                kind = "card-named field"
                raise CardholderDataDetected(f"{path}.{key}", kind)
            _scan(child, f"{path}.{key}")
        return
    if isinstance(value, list):
        for i, child in enumerate(value):
            _scan(child, f"{path}[{i}]")
        return
    if isinstance(value, str) and _contains_pan_in_text(value):
        raise CardholderDataDetected(path, "PAN-class digit run")


def contains_cardholder_data(obj: Any) -> bool:
    try:
        _scan(obj, "$")
        return False
    except CardholderDataDetected:
        return True


def forbid_cardholder_data_body(surface: str = "request"):
    """Dependency factory: scan the raw JSON body before pydantic ever parses it.

    Usage: `Depends(forbid_cardholder_data_body("edge.ingest"))`.
    Raises HTTPException(422) with a sanitized detail that never echoes the PAN.
    """

    async def dependency(request: Request) -> None:
        raw = await request.body()
        if not raw:
            return None
        try:
            parsed = json.loads(raw)
        except JSONDecodeError:
            return None  # not JSON; the route's own validation will reject it
        try:
            _scan(parsed, "$")
        except CardholderDataDetected as exc:
            raise HTTPException(
                status_code=422,
                detail=f"VE-JIT does not accept cardholder data; request rejected before processing ({exc.kind} at {exc.path})",
            ) from exc
        return None

    return dependency


def redact(value: Any) -> Any:
    """Redact card-named fields and Luhn-passing digit runs (for logs/diagnostics)."""
    if isinstance(value, dict):
        out = {}
        for key, child in value.items():
            if KEY_NAMES.search(str(key).lower()):
                out[key] = "[REDACTED-CARD-FIELD]"
            else:
                out[key] = redact(child)
        return out
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        def _mask(match: re.Match) -> str:
            return "[REDACTED-PAN]" if luhn_ok(_digits(match.group(1))) else match.group(0)

        return PAN_DIGIT_RUN.sub(_mask, value)
    return value


def pci_scope_posture() -> dict:
    """Live posture artifact for the compliance surface (no fabricated metrics)."""
    return {
        "scope": "out-of-scope",
        "statement": "VE-JIT processes non-PCI SKU/inventory/telemetry data only and is not part of the cardholder data environment.",
        "ingests": [
            "SKU-level sale/return facts (post-authorization, no PANs)",
            "Inventory on-hand, receipts, counts, transfers, waste",
            "Lane telemetry (queue lengths, wait times, device heartbeats)",
            "Supplier performance (fill-rate, OTIF) and procurement data",
        ],
        "never_ingests": [
            "PAN (primary account number)",
            "Track 1 / Track 2 / sensitive authentication data",
            "CVV / CVC / card security codes",
            "Card expiration and cardholder name as payment context",
        ],
        "enforcement": [
            "HTTP 422 rejection of any request body carrying card-named fields or Luhn-valid PAN runs (app/pci_guard.py)",
            "Audit middleware redacts PAN-class material from the on-disk request log",
            "Tests assert no cardholder data path reaches storage, logs or model prompts",
        ],
        "controls": [
            "systemd sandbox: vendarix-edge.service + vendarix-hw-adapter.service (MemoryMax, NoNewPrivileges, AF_UNIX-only, ProtectSystem=strict)",
            "Append-only hash-chained audit trail (edge_events, inventory_ledger, edge_continuity_records)",
            "Ed25519-signed policy bundles with monotonic versions; SAFE_MODE hard stop on invariant failure",
        ],
        "operator_actions": [
            "Segment POS lane / CDE VLANs from the back-office VLAN carrying VE-JIT",
            "Restrict edge UDS socket permissions (0660 vendarix-hw:vendarix-edge)",
            "Do not mount POS payment pinpad serial/USB devices into the edge runtime",
            "Rotate and scrub any stray card data found in logs (tests/PCI_SCOPE.md playbook)",
        ],
        "qsa_note": "VE-JIT processes no cardholder data; scoping follows the 'no CHD/SAD, no CDE' determination. Re-validate on any new integration.",
        "reference": "docs/PCI_SCOPE.md",
        "generated_by": "ve-jit compliance posture v1",
        "posture_id": "vejit-pci-scope-v1",
    }
