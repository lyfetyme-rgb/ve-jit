#!/usr/bin/env python3
"""Secret scanner for the VE-JIT repository.

Fails CI if anything that looks like production signing material or credentials
is committed to the tree. Contractors and CI must only ever see ephemeral
test keys generated at test runtime.

Detection classes:
  1. PEM / OpenSSH private key blocks
  2. Sensitive names assigned a real-looking value (POLICY_SIGNING_KEY,
     CLOUD_SIGNER_TOKEN, ..., DATABASE_URL with credentials)
  3. Generic high-entropy values assigned to *token*/*secret*/*password*/*key* names

Placeholder values (change-me, your_..., <...>, example, dummy, test/local/dev) are
explicitly permitted. SQLite URLs without credentials are permitted (no secrets).
"""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PEM_BLOCK = re.compile(r"-----BEGIN (?:OPENSSH|RSA|EC|PGP|ENCRYPTED|PRIVATE|OPENPGP) PRIVATE KEY(?: BLOCK)?-----")

NAME_VALUE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_\-]*)\s*[:=]\s*(.*)$")
QUOTED = re.compile(r'"([^"]*)"|\'([^\']*)\'')
TOKENISH = re.compile(r"^([A-Za-z0-9+/=_\-\.]{20,})\s*$")

SENSITIVE_NAMES = {
    "POLICY_SIGNING_KEY", "CLOUD_SIGNER_TOKEN", "CLOUD_SYNC_TOKEN", "CLOUD_SYNC_API_KEY",
    "SECRET_KEY", "JWT_SECRET", "JWT_SIGNING_KEY", "REDIS_PASSWORD", "POSTGRES_PASSWORD",
    "DB_PASSWORD", "DATABASE_URL", "DATABASE_ROOT_PASSWORD", "GF_SECURITY_ADMIN_PASSWORD",
    "VAULT_TOKEN", "AWS_SECRET_ACCESS_KEY", "SMTP_PASSWORD", "WEBHOOK_SECRET",
}
GENERIC_NAME = re.compile(r"(?i)(?:^|[_\-])(api[_-]?key|secret|token|password|private[_-]?key|credential)(?:[_\-]|$)")

PLACEHOLDER = re.compile(
    r"(?i)^(changeme|change-me|change_me|replace[-_]?me|your[-_].*|example|placeholder|dummy|"
    r"test[-_]|ci[-_]|local[-_]|dev[-_]|x{3,}|\.{3,}|<.*>|CHANGE_ME|TODO|not-a-|notasecret|"
    r"dummy[-_]?token|sample[-_]|sqlite://|postgres(?:ql)?://\w+:\w+@localhost|postgres(?:ql)?://\w+:\w+@127\.0\.0\.1|"
    r"postgres(?:ql)?://[^@:\s]+:[^@\s]*@localhost|postgres(?:ql)?://[^@:\s]+:[^@\s]*@127\.0\.0\.1)"
)

ALLOWLIST = {
    "backend/.env.example",
    "tools/gen_policy_bundle_key.py",
    "tools/scan_secrets.py",
}


def tracked_files() -> list[Path]:
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=ROOT,
            capture_output=True,
            check=True,
        ).stdout
        return [ROOT / p.decode("utf-8", "replace") for p in out.split(b"\x00") if p]
    except (FileNotFoundError, subprocess.CalledProcessError):
        skip_dirs = {".git", "node_modules", ".venv", ".venv-win", "dist", "build", ".next", "__pycache__"}
        results = []
        for path in ROOT.rglob("*"):
            if not path.is_file():
                continue
            if any(part in skip_dirs for part in path.parts):
                continue
            results.append(path)
        return results


def is_placeholder(value: str) -> bool:
    return bool(PLACEHOLDER.match(value.strip()))


def looks_credentialed_url(value: str) -> bool:
    return "://" in value and "@" in value and not is_placeholder(value)


def scan_file(path: Path) -> list[str]:
    rel = path.relative_to(ROOT).as_posix()
    if rel in ALLOWLIST:
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeError):
        return []

    findings: set[str] = set()
    if PEM_BLOCK.search(text):
        findings.add("private key block")

    for lineno, line in enumerate(text.splitlines(), start=1):
        match = NAME_VALUE.match(line)
        if not match:
            continue
        name, rest = match.group(1), match.group(2)

        quoted = QUOTED.search(rest)
        if quoted:
            value = next(g for g in quoted.groups() if g is not None)
        else:
            token = rest.strip().split()[0] if rest.strip() else ""
            value = token.strip(",;").strip('"').strip("'")

        if not value:
            continue  # empty string is never a secret

        if name in SENSITIVE_NAMES:
            if looks_credentialed_url(value):
                findings.add(f"{name} = <credentialed URL>")
            elif not is_placeholder(value):
                # Real value in a sensitive slot
                findings.add(f"{name} line {lineno} = <non-placeholder value>")
            continue

        if GENERIC_NAME.search(name) and not is_placeholder(value):
            candidate = value.strip('"').strip("'")
            if TOKENISH.match(candidate):
                findings.add(f"{name} line {lineno} = <high-entropy value>")

    return sorted(findings)


def main() -> int:
    problems: list[tuple[str, list[str]]] = []
    for path in tracked_files():
        findings = scan_file(path)
        if findings:
            problems.append((path.relative_to(ROOT).as_posix(), findings))
    if problems:
        print("FAIL: potential secret material found in tracked files")
        for rel, findings in problems:
            for finding in findings:
                print(f"  {rel}: {finding}")
        print("\nPolicy: production signing keys (POLICY_SIGNING_KEY, CLOUD_SIGNER_TOKEN,")
        print("CLOUD_SYNC_TOKEN, ...) must never be committed. Use ephemeral test keys")
        print("generated at test runtime (see tools/gen_policy_bundle_key.py).")
        return 1
    print(f"PASS: no secret material found in {len(tracked_files())} tracked files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
