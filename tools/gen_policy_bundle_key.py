#!/usr/bin/env python3
"""Generate an Ed25519 policy-signing keypair.

Prints a fresh seed (private) and public key, both base64url-encoded.
Store the seed in your secret manager (e.g. CLOUD_SIGNER_TOKEN + POLICY_SIGNING_KEY
or a vault); never commit it. The public key goes into the edge trust ring:

    POST /api/v1/edge/policy/keys  {"kid": "vendarix-pol-2026-12", "public_key_b64": "<pub>", "role": "staged", "actor": "ops"}
    # when ready:
    POST /api/v1/edge/policy/keys/vendarix-pol-2026-12/activate  {"actor": "ops"}

Usage: python tools/gen_policy_bundle_key.py
"""

import base64

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat


def main() -> None:
    key = Ed25519PrivateKey.generate()
    seed = key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    public = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    print("# Policy bundle signing keypair (Ed25519)")
    print(f"POLICY_SIGNING_KEY={base64.urlsafe_b64encode(seed).decode()}")
    print(f"public_key_b64={base64.urlsafe_b64encode(public).decode()}")
    print("# Keep POLICY_SIGNING_KEY secret. Register public_key_b64 in the edge trust ring.")


if __name__ == "__main__":
    main()
