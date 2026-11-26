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

        # Multi-tenant scoping added for the Store Console; nullable for older rows.
        for column, ddl in (
            ("tenant_id", "tenant_id TEXT"),
            ("store_id", "store_id TEXT"),
        ):
            try:
                conn.execute(text(f"SELECT {column} FROM recommendations LIMIT 0"))
            except Exception:
                conn.execute(text(f"ALTER TABLE recommendations ADD COLUMN {ddl}"))

        # Priority lane added to the Atlassian outbox after the first release.
        try:
            conn.execute(text("SELECT priority FROM int_atlassian_outbox LIMIT 0"))
        except Exception:
            conn.execute(text("ALTER TABLE int_atlassian_outbox ADD COLUMN priority INTEGER NOT NULL DEFAULT 3"))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
