"""Migration runner smoke tests."""

import os
import subprocess
import sys
import tempfile
import uuid

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MIGRATE = os.path.join(REPO, "infrastructure", "migrations", "migrate.py")


def fresh_db(tmp_path):
    """Create a database with all application tables via the ORM, then migrate."""
    import os

    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'edge.db'}"
    from sqlalchemy import create_engine

    from app.db.database import Base
    import app.planes.models  # noqa: F401
    import app.edge.models  # noqa: F401
    import app.cloud.models  # noqa: F401

    engine = create_engine(os.environ["DATABASE_URL"], connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    engine.dispose()
    os.environ.pop("DATABASE_URL", None)
    return str(tmp_path / "edge.db")


def run(db, *args):
    return subprocess.run([sys.executable, MIGRATE, "--db", db, *args], capture_output=True, text=True, timeout=30)


def test_migrate_then_status_then_migrate_again_is_noop(tmp_path):
    db = fresh_db(tmp_path)
    first = run(db)
    assert first.returncode == 0, first.stderr
    assert "applied" in first.stdout

    status = run(db, "--status")
    assert status.returncode == 0
    assert "applied" in status.stdout
    assert "pending" not in status.stdout

    second = run(db)
    assert second.returncode == 0
    assert "nothing to apply" in second.stdout


def test_append_only_trigger_rejects_update(tmp_path):
    db = fresh_db(tmp_path)
    result = run(db)
    assert result.returncode == 0

    import sqlite3

    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO inventory_ledger(sku, store_id, delta, reason, actor) VALUES ('DAIRY-001', 'store_001', -1, 'SALE', 'test')")
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE inventory_ledger SET sku = 'X'")
    conn.close()


def test_baseline_marks_migrations_applied(tmp_path):
    db = fresh_db(tmp_path)
    result = run(db, "--baseline")
    assert result.returncode == 0
    status = run(db, "--status")
    assert "pending" not in status.stdout
