# Queue Management Integration

This repository includes the stronger queue-management engine merged from the uploaded queue-management fileset.

## Added
- `backend/app/core/queue_management_ai.py`
- `backend/tests/test_queue_management.py`
- startup queue monitor wiring in `app.main`
- `/api/v1/queue/recommendation`
- `/api/v1/queue/status/{store_id}`

## Runtime behavior
On startup, VE-JIT launches a background queue monitor using a demo data provider for `store_001`.
