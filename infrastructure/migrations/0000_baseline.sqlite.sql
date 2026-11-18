-- 0000_baseline: marker migration. The current schema source of truth is the
-- application's models in backend/app (edge/models.py, cloud/models.py,
-- planes/models.py, db/models.py) plus backend/app/db/database.py:create_all.
-- This file exists so schema histories can identify "introduced by the application".
SELECT 1;
