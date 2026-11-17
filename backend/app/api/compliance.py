from pathlib import Path

from fastapi import APIRouter

from app.core.compliance import ComplianceRegistry

router = APIRouter(prefix="/api/v1/compliance", tags=["compliance"])
registry = ComplianceRegistry()


@router.get("/status")
def compliance_status():
    return registry.status()


@router.get("/pci-scope")
def pci_scope():
    """Live out-of-scope PCI posture artifact (static statement + enforcement pointers)."""
    from app.pci_guard import pci_scope_posture

    return pci_scope_posture()


@router.get("/audit-log-info")
def audit_log_info():
    log_path = Path("audit_logs/audit.log")
    return {
        "path": str(log_path),
        "exists": log_path.exists(),
        "size_bytes": log_path.stat().st_size if log_path.exists() else 0,
    }
