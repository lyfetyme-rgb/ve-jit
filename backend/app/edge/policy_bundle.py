"""Policy bundle installation, trust ring management and gate integration."""

import json
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.edge.models import EdgePolicyBundle, EdgePolicyKey
from app.edge.policy_verifier import (
    PolicyVerificationError,
    load_public_key_b64,
    verify_edge_policy_bundle,
)


class BundleError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _trusted_ring(db: Session) -> dict[str, Any]:
    ring: dict[str, Any] = {}
    for key in db.query(EdgePolicyKey).filter(EdgePolicyKey.role.in_(["active", "staged"])).all():
        ring[key.kid] = load_public_key_b64(key.public_key_b64)
    return ring


def list_keys(db: Session) -> list[dict]:
    return [
        {
            "kid": k.kid,
            "public_key_b64": k.public_key_b64,
            "role": k.role,
            "added_by": k.added_by,
            "created_at": k.created_at.isoformat() if k.created_at else None,
            "retired_at": k.retired_at.isoformat() if k.retired_at else None,
        }
        for k in db.query(EdgePolicyKey).order_by(EdgePolicyKey.id.asc()).all()
    ]


def add_key(db: Session, *, kid: str, public_key_b64: str, role: str, added_by: str) -> dict:
    kid = kid.strip()
    if not kid:
        raise BundleError(422, "kid must be non-empty")
    if role not in ("staged", "active"):
        raise BundleError(422, "role must be 'staged' or 'active' (retire goes through the retire endpoint)")
    load_public_key_b64(public_key_b64)  # validates 32-byte ed25519 key
    existing = db.query(EdgePolicyKey).filter(EdgePolicyKey.kid == kid).first()
    if existing is not None:
        raise BundleError(409, f"key {kid!r} already exists (role={existing.role})")
    key = EdgePolicyKey(kid=kid, public_key_b64=public_key_b64, role=role, added_by=added_by.strip())
    db.add(key)
    db.commit()
    db.refresh(key)
    return {"kid": key.kid, "role": key.role, "public_key_b64": key.public_key_b64}


def retire_key(db: Session, *, kid: str, actor: str) -> dict:
    key = db.query(EdgePolicyKey).filter(EdgePolicyKey.kid == kid).first()
    if key is None:
        raise BundleError(404, f"key {kid!r} not found")
    if key.role == "retired":
        return {"kid": key.kid, "role": key.role, "changed": False}
    key.role = "retired"
    key.retired_at = datetime.now(timezone.utc)
    db.commit()
    return {"kid": key.kid, "role": key.role, "changed": True, "retired_by": actor}


def activate_key(db: Session, *, kid: str, actor: str) -> dict:
    key = db.query(EdgePolicyKey).filter(EdgePolicyKey.kid == kid).first()
    if key is None:
        raise BundleError(404, f"key {kid!r} not found")
    if key.role == "retired":
        raise BundleError(409, f"key {kid!r} is retired; publish a fresh key instead")
    key.role = "active"
    db.commit()
    return {"kid": key.kid, "role": key.role, "changed": True, "activated_by": actor}


def install_bundle(db: Session, *, bundle: dict[str, Any], actor: str, max_age_seconds: int) -> dict:
    if not actor or not actor.strip():
        raise BundleError(422, "a non-empty actor is required to install a policy bundle")
    ring = _trusted_ring(db)
    try:
        unsigned = verify_edge_policy_bundle(bundle, ring, max_age_seconds=max_age_seconds)
    except PolicyVerificationError as exc:
        raise BundleError(409, f"policy bundle verification failed: {exc}") from exc

    existing = db.query(EdgePolicyBundle).order_by(EdgePolicyBundle.policy_version.desc()).first()
    current_version = existing.policy_version if existing else 0
    if int(unsigned["policy_version"]) <= current_version:
        raise BundleError(
            409,
            f"policy_version must be strictly increasing: bundle is {unsigned['policy_version']}, current is {current_version}",
        )

    issued_raw = str(unsigned["issued_at"]).replace("Z", "+00:00")
    expires_raw = str(unsigned["expires_at"]).replace("Z", "+00:00")
    record = EdgePolicyBundle(
        bundle_id=unsigned.get("bundle_id") or f"pol_{unsigned['policy_version']}",
        policy_version=int(unsigned["policy_version"]),
        key_id=unsigned["key_id"],
        tenant_id=str(unsigned.get("tenant_id", ""))[:64],
        store_id=str(unsigned.get("store_id", ""))[:64],
        issued_at=datetime.fromisoformat(issued_raw),
        expires_at=datetime.fromisoformat(expires_raw),
        signature=str(bundle["signature"]),
        bundle=json.dumps(bundle, sort_keys=True, separators=(",", ":")),
        installed_by=actor.strip(),
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return bundle_to_dict(record)


def current_bundle(db: Session) -> Optional[dict]:
    record = db.query(EdgePolicyBundle).order_by(EdgePolicyBundle.policy_version.desc()).first()
    return bundle_to_dict(record) if record else None


def current_bundle_row(db: Session) -> Optional[EdgePolicyBundle]:
    return db.query(EdgePolicyBundle).order_by(EdgePolicyBundle.policy_version.desc()).first()


def verify_current_bundle(db: Session, *, max_age_seconds: int) -> tuple[bool, str]:
    """Re-verify the installed bundle for the hard safety gate."""
    record = current_bundle_row(db)
    if record is None:
        return False, "no signed policy bundle installed; relying on local rule set (fallback)"
    ring = _trusted_ring(db)
    try:
        bundle = json.loads(record.bundle)
        verify_edge_policy_bundle(bundle, ring, max_age_seconds=max_age_seconds)
    except (PolicyVerificationError, ValueError) as exc:
        return False, f"installed bundle failed verification: {exc}"
    return True, f"signed bundle v{record.policy_version} (kid={record.key_id}) verified"


def bundle_to_dict(record: EdgePolicyBundle) -> dict:
    return {
        "bundle_id": record.bundle_id,
        "policy_version": record.policy_version,
        "key_id": record.key_id,
        "tenant_id": record.tenant_id,
        "store_id": record.store_id,
        "issued_at": record.issued_at.isoformat() if record.issued_at else None,
        "expires_at": record.expires_at.isoformat() if record.expires_at else None,
        "installed_by": record.installed_by,
        "installed_at": record.installed_at.isoformat() if record.installed_at else None,
        "bundle": json.loads(record.bundle),
    }
