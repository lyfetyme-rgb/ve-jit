#!/usr/bin/env python3
"""Generate one LOCAL_USERS_JSON entry with a salted PBKDF2 password hash.

The password is read from a prompt (never from argv or this file). The output
is printed to stdout only — store it in your secret manager / environment,
not in the repository.

Example:
    python tools/gen_local_user.py alice store_manager foodlion FL-VA-042 \
        | jq -c '[.]'   # -> value for LOCAL_USERS_JSON
"""

import argparse
import base64
import getpass
import hashlib
import json
import os

ITERATIONS = 310_000
SCHEME = "pbkdf2_sha256"


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS)
    return "$".join((
        SCHEME,
        str(ITERATIONS),
        base64.urlsafe_b64encode(salt).decode("ascii"),
        base64.urlsafe_b64encode(derived).decode("ascii"),
    ))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("username")
    parser.add_argument(
        "role",
        choices=[
            "operator",
            "store_manager",
            "operations_manager",
            "regional_manager",
            "system_admin",
        ],
    )
    parser.add_argument("tenants", nargs="*", help="tenant ids this user may access")
    parser.add_argument("--stores", default="", help="comma-separated store ids")
    args = parser.parse_args()

    password = getpass.getpass(f"Password for {args.username}: ")
    if len(password) < 12:
        parser.error("password must be at least 12 characters")

    entry = {
        "username": args.username,
        "password_hash": hash_password(password),
        "role": args.role,
        "tenants": args.tenants,
        "stores": [s.strip() for s in args.stores.split(",") if s.strip()],
    }
    print(json.dumps(entry))


if __name__ == "__main__":
    main()
