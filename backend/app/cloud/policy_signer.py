"""Cloud policy signer — the only party that signs edge policy bundles.

Bundles carry a JWKS-style key_id, a monotonic policy_version, issued_at /
expires_at, and an Ed25519 signature over the canonicalized bundle bytes
(RFC-8785-style canonical JSON: sorted keys, no whitespace, UTF-8).

The signer never contacts edges; bundles are delivered through the normal
policy distribution channel. Edge-side verification lives in
app/edge/policy_verifier.py with an overlapping active+staged trust ring so
stores never get stranded during rotation.
"""

import base64
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def canonical_bundle_bytes(bundle_without_sig: dict[str, Any]) -> bytes:
    """Deterministic RFC-8785-style canonical JSON serialization."""
    return json.dumps(
        bundle_without_sig,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def sign_policy_bundle(
    payload: dict[str, Any],
    key_id: str,
    private_key: Ed25519PrivateKey,
    policy_version: int,
    ttl_seconds: int = 86400,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    unsigned = {
        **payload,
        "policy_version": int(policy_version),
        "key_id": key_id,
        "issued_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expires_at": (now + timedelta(seconds=ttl_seconds)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    raw = canonical_bundle_bytes(unsigned)
    sig_b64 = base64.b64encode(private_key.sign(raw)).decode("ascii")
    return {**unsigned, "signature": sig_b64}


def fingerprint(public_bytes: bytes) -> str:
    import hashlib

    return hashlib.sha256(public_bytes).hexdigest()[:16]


def load_private_key_from_seed_b64(seed_b64: str) -> Ed25519PrivateKey:
    raw = base64.urlsafe_b64decode(seed_b64)
    if len(raw) != 32:
        raise ValueError("Ed25519 private seed must decode to exactly 32 bytes")
    return Ed25519PrivateKey.from_private_bytes(raw)


def private_key_to_seed_b64(private_key: Ed25519PrivateKey) -> str:
    from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, NoEncryption

    raw = private_key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    return base64.urlsafe_b64encode(raw).decode("ascii")


def public_key_b64(private_key: Ed25519PrivateKey) -> str:
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    raw = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return base64.urlsafe_b64encode(raw).decode("ascii")
