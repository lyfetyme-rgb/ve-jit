# VE-JIT Migrations

Forward-only, numbered SQL migrations. Applied by `python infrastructure/migrations/migrate.py
--db <path>` (or via the app container with `DATABASE_URL` set).

| File | Contents |
|---|---|
| `0000_baseline.*.sql` | Baseline marker; points operators at `backend/app/*/models.py` as the current schema source of truth |
| `0001_append_only_triggers.sqlite.sql` | INSERT-only triggers for event store + audit ledger tables |
| `0001_append_only_triggers.postgres.sql` | PostgreSQL variant of 0001 (reserved slot; applied only with `--driver pg`) |
| `0002_cloud_reconciliation.sqlite.sql` | Cloud watermark / dedup / quarantine / inventory projection tables |

Rules:
- Never edit an applied migration — add a new numbered file instead.
- Idempotency is required: every statement must be safe to re-apply (IF NOT EXISTS / IF EXISTS / CREATE OR REPLACE).
- Apply migrations in a transaction; a failed migration leaves the database unchanged (SQLite).
- `migrate.py --status` lists applied migrations; `migrate.py --baseline` records all migrations as applied on an existing database without running them.
- **Fresh databases**: start the app once first (or run `Base.metadata.create_all`) so the application tables exist, then apply 0001+. 0001 creates triggers on existing tables and is not standalone.

```bash
python infrastructure/migrations/migrate.py --db /var/lib/vendarix/edge.db
python infrastructure/migrations/migrate.py --db /var/lib/vendarix/edge.db --status
python infrastructure/migrations/migrate.py --db /var/lib/vendarix/edge.db --baseline
```
