# Incident Response Runbook

## Severity Levels
- SEV1: Production outage or suspected breach
- SEV2: Major feature outage
- SEV3: Degraded service

## Initial steps
1. Open incident channel
2. Assign incident commander
3. Freeze risky deployments
4. Review logs, metrics, and audit records
5. Contain affected systems
6. Communicate status updates

## Evidence sources
- audit_logs/audit.log
- container logs
- Prometheus/Grafana
- infrastructure change history
