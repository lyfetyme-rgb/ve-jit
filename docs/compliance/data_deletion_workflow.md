# Data Deletion Workflow

## Trigger events
- Customer contract termination
- Approved deletion request
- Data retention expiry

## Workflow
1. Verify request authority
2. Identify affected tenant/store records
3. Snapshot system state if legally required
4. Delete or anonymize relevant records
5. Record completion in audit logs
6. Confirm completion to requesting party

## Note
Network intelligence datasets must only retain anonymized aggregated values.
