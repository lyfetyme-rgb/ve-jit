from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from app.config import settings


connect_args = {"check_same_thread": False} if settings.DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def ensure_version_columns() -> None:
    """Add optimistic-concurrency `version` columns to pre-guardrail databases.

    Idempotent: a database created before the six-domain guardrails gets the
    column with default 1; a fresh one already has it via create_all.
    """
    from sqlalchemy import text

    tables = (
        "inventory_items",
        "purchase_orders",
        "transfer_orders",
        "commerce_orders",
        "loyalty_members",
        "recommendations",
    )
    with engine.begin() as conn:
        for table in tables:
            try:
                conn.execute(text(f"SELECT version FROM {table} LIMIT 0"))
            except Exception:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN version INTEGER NOT NULL DEFAULT 1"))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
