#!/usr/bin/env python3
"""Load a retailer JSON catalog into a VE-JIT database.

Idempotent: re-running with the same catalog makes no duplicate rows. Any
company can be loaded — the shipped demo catalog contains generic archetypes
only, with no real retailer names.

Examples:
    python tools/load_retailers.py config/my-retailers.json
    python tools/load_retailers.py config/my-retailers.json --db postgresql://user:pass@host/db
"""

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.db.database import Base  # noqa: E402
import app.cloud.models  # noqa: E402,F401
import app.edge.models  # noqa: E402,F401
import app.integrations.models  # noqa: E402,F401
import app.planes.models  # noqa: E402,F401
from app.console.models import ConsoleStore, ConsoleTenant  # noqa: E402,F401
from app.console.seed import load_retailer_config, seed_retailers  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "config",
        nargs="?",
        default=None,
        help="catalog JSON path (default: RETAILER_CONFIG or config/retailers.demo.json)",
    )
    parser.add_argument(
        "--db",
        default=os.environ.get("DATABASE_URL", "sqlite:///./vejit.db"),
        help="SQLAlchemy database URL",
    )
    args = parser.parse_args()

    catalog = load_retailer_config(args.config)
    engine = create_engine(args.db)
    Base.metadata.create_all(bind=engine)  # safe on an empty database
    session = sessionmaker(bind=engine)
    db = session()
    try:
        created = seed_retailers(db, catalog)
    finally:
        db.close()

    names = ", ".join(r["name"] for r in catalog["retailers"])
    print(f"Loaded {names}")
    print(
        "Created: "
        + ", ".join(f"{key}={value}" for key, value in created.items())
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
