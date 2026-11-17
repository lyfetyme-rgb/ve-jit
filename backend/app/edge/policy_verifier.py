"""Edge-side policy bundle verification with an overlapping trust ring.

The edge keeps a small set of trusted Ed25519 public keys (active + staged
next). Bundles are accepted only when: key_id is trusted, the Ed25519
signature over the canonical bytes verifies, the bundle is unexpired, and it
did not exceed POLICY_BUNDLE_MAX_AGE_SECONDS.

Rotation is three stages: publish-next-key -> cut-over signing -> retire old
(after the last 2026-rotation bundle expires). See docs/EDGE_ARCHITECTURE.md.
"""

import base64
from datetime import datetime, timezone
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from app.cloud.policy_signer import canonical_bundle_bytes


class PolicyVerificationError(Exception):
    pass


def load_public_key_b64(public_b64: str) -> Ed25519PublicKey:
    try:
        raw = base64.urlsafe_b64decode(public_b64)
    except Exception as exc:
        raise PolicyVerificationError("public key is not valid base64url") from exc
    if len(raw) != 32:
        raise PolicyVerificationError("Ed25519 public key must be exactly 32 bytes")
    try:
        return Ed25519PublicKey.from_public_bytes(raw)
    except Exception as exc:
        raise PolicyVerificationError("not a valid Ed25519 public key") from exc


def verify_edge_policy_bundle(
    bundle: dict[str, Any],
    trusted_public_keys: dict[str, Ed25519PublicKey],
    max_age_seconds: int = 86400,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Verify a signed policy bundle; return the unsigned body if valid."""
    now = now or datetime.now(timezone.utc)
    key_id = bundle.get("key_id")
    sig_b64 = bundle.get("signature")

    if not key_id or key_id not in trusted_public_keys:
        raise PolicyVerificationError(f"untrusted or unknown key_id: {key_id!r}")
    if not sig_b64:
        raise PolicyVerificationError("missing Ed25519 signature")

    unsigned = {k: v for k, v in bundle.items() if k != "signature"}
    try:
        signature = base64.b64decode(sig_b64, validate=True)
    except Exception as exc:
        raise PolicyVerificationError("signature is not valid base64") from exc

    try:
        trusted_public_keys[key_id].verify(signature, canonical_bundle_bytes(unsigned))
    except InvalidSignature as exc:
        raise PolicyVerificationError("Ed25519 signature check failed") from exc

    try:
        issued_at = datetime.fromisoformat(str(unsigned["issued_at"]).replace("Z", "+00:00"))
        expires_at = datetime.fromisoformat(str(unsigned["expires_at"]).replace("Z", "+00:00"))
    except (KeyError, ValueError) as exc:
        raise PolicyVerificationError("issued_at/expires_at missing or malformed") from exc

    if now > expires_at:
        raise PolicyVerificationError("policy bundle lease expired")
    if max_age_seconds > 0 and (now - issued_at).total_seconds() > max_age_seconds:
        raise PolicyVerificationError("policy bundle exceeded POLICY_BUNDLE_MAX_AGE_SECONDS")

    return unsigned
