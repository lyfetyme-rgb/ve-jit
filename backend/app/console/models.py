"""Tenant and store registry for the Store Console."""

from sqlalchemy import Column, Integer, String
from sqlalchemy.sql import func

from app.db.database import Base


class ConsoleTenant(Base):
    __tablename__ = "console_tenants"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(String(64), unique=True, nullable=False, index=True)
    name = Column(String(128), nullable=False)
    vertical = Column(String(64), nullable=False)  # grocery | hardware | home_improvement
    description = Column(String(512), nullable=True)


class ConsoleStore(Base):
    __tablename__ = "console_stores"

    id = Column(Integer, primary_key=True, index=True)
    store_id = Column(String(64), unique=True, nullable=False, index=True)
    tenant_id = Column(String(64), nullable=False, index=True)
    name = Column(String(128), nullable=False)
    location = Column(String(128), nullable=True)
    architecture = Column(String(16), nullable=False, default="amd64")
    channel = Column(String(32), nullable=False, default="store")
