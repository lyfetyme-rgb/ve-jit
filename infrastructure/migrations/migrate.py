#!/usr/bin/env python3
"""Dependency-free migration runner for VE-JIT.

Tracks applied migrations in the `schema_migrations` table. Idempotent:
re-running is a no-op; existing installs may record all migrations as applied
without running them via `--baseline`.

Usage:
  python infrastructure/migrations/migrate.py --db /var/lib/vendarix/edge.db
  python infrastructure/migrations/migrate.py --db /var/lib/vendarix/edge.db --status
  python infrastructure/migrations/migrate.py --db :memory: --status
"""

import argparse
import sqlite3
import sys
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parent
SUFFIX = ".sqlite.sql"


def discover(driver: str = "sqlite") -> list[tuple[str, Path]]:
    suffix = ".sqlite.sql" if driver == "sqlite" else ".postgres.sql"
    files = sorted(MIGRATIONS_DIR.glob(f"0*{suffix}"))
    return [(p.name.split("_")[0], p) for p in files]


def ensure_ledger(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "  version TEXT PRIMARY KEY,"
        "  applied_at TEXT NOT NULL DEFAULT (datetime('now'))"
        ")"
    )


def applied(conn: sqlite3.Connection) -> set[str]:
    ensure_ledger(conn)
    return {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}


def cmd_baseline(conn: sqlite3.Connection, driver: str) -> int:
    ensure_ledger(conn)
    done = applied(conn)
    added = 0
    for version, _path in discover(driver):
        if version not in done:
            conn.execute("INSERT INTO schema_migrations(version) VALUES (?)", (version,))
            added += 1
    conn.commit()
    print(f"baseline: recorded {added} migration(s) as applied")
    return 0


def cmd_status(conn: sqlite3.Connection, driver: str) -> int:
    done = applied(conn)
    for version, path in discover(driver):
        mark = "applied" if version in done else "pending"
        print(f"  {version}  {mark}  {path.name}")
    return 0


def cmd_migrate(conn: sqlite3.Connection, driver: str) -> int:
    done = applied(conn)
    pending = [(v, p) for v, p in discover(driver) if v not in done]
    if not pending:
        print("migrations: nothing to apply")
        return 0
    for version, path in pending:
        sql = path.read_text(encoding="utf-8")
        print(f"applying {path.name} ...")
        try:
            with conn:  # transaction: either the whole migration lands or none of it
                conn.executescript(sql)
                conn.execute("INSERT INTO schema_migrations(version) VALUES (?)", (version,))
        except sqlite3.Error as exc:
            print(f"FAILED at {path.name}: {exc}", file=sys.stderr)
            print("database unchanged (transaction rolled back)", file=sys.stderr)
            return 1
    print(f"migrations: applied {len(pending)} migration(s)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, help="SQLite database path (or :memory:)")
    ap.add_argument("--driver", default="sqlite", choices=["sqlite", "postgres"])
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--baseline", action="store_true")
    args = ap.parse_args()

    if args.driver != "sqlite":
        print("postgres driver is a reserved slot; run with --driver sqlite on this host", file=sys.stderr)
        return 2

    conn = sqlite3.connect(args.db)
    try:
        if args.status:
            return cmd_status(conn, args.driver)
        if args.baseline:
            return cmd_baseline(conn, args.driver)
        return cmd_migrate(conn, args.driver)
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
