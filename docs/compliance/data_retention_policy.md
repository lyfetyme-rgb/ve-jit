# Data Retention Policy

## Purpose
Define how VE-JIT stores, rotates, archives, and deletes operational data.

## Default retention targets
- Audit logs: 365 days online, then archive or purge
- Queue and operational metrics: 180 days
- Inventory and transaction summaries: 365 days minimum
- Security events: 365 days minimum
- Backups: 35 days rolling

## Rules
- No customer PII should be retained unless explicitly required by a contracted workflow.
- Employee-identifying data must not be exported into network-intelligence datasets.
- Aggregated anonymized benchmarking data may be retained for model improvement.
