from sqlalchemy import Column, Integer, String, Float, DateTime
from sqlalchemy.sql import func
from app.db.database import Base


class InventoryItem(Base):
    __tablename__ = "inventory_items"

    id = Column(Integer, primary_key=True, index=True)
    sku = Column(String(100), unique=True, index=True, nullable=False)
    name = Column(String(255), nullable=False)
    quantity = Column(Integer, nullable=False, default=0)
    reorder_point = Column(Integer, nullable=False, default=10)
    unit_cost = Column(Float, nullable=False, default=0.0)
    version = Column(Integer, nullable=False, server_default="1", default=1)  # optimistic concurrency (CAS)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
