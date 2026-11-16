from dataclasses import dataclass
from typing import Dict, List


@dataclass
class ComplianceControl:
    name: str
    status: str
    description: str


class ComplianceRegistry:
    def __init__(self) -> None:
        self.frameworks: Dict[str, List[ComplianceControl]] = {
            "soc2_foundation": [
                ComplianceControl("access_control", "implemented", "Role-based starter auth and access controls."),
                ComplianceControl("audit_logging", "implemented", "Request audit logging middleware enabled."),
                ComplianceControl("secrets_management", "partial", "Template support present; production secret manager required."),
                ComplianceControl("incident_response", "partial", "Runbooks and policies required for production team operations."),
            ],
            "iso27001_foundation": [
                ComplianceControl("asset_management", "implemented", "Repository structure and deployment artifacts documented."),
                ComplianceControl("logging_monitoring", "implemented", "Audit logs, Prometheus, Grafana are present."),
                ComplianceControl("supplier_risk", "partial", "Vendor/legal templates included, formal review process needed."),
            ],
            "privacy_foundation": [
                ComplianceControl("data_minimization", "implemented", "Network intelligence model excludes customer and employee PII."),
                ComplianceControl("retention_policy", "implemented", "Data retention policy template included."),
                ComplianceControl("deletion_workflow", "partial", "Operational deletion workflow template included; automation still needed."),
            ],
        }

    def status(self) -> Dict[str, List[dict]]:
        return {
            framework: [control.__dict__ for control in controls]
            for framework, controls in self.frameworks.items()
        }
