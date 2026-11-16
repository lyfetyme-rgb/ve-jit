from pydantic import BaseModel, Field


class InventoryCreate(BaseModel):
    sku: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    quantity: int = 0
    reorder_point: int = 10
    unit_cost: float = 0.0


class InventoryUpdate(BaseModel):
    quantity_delta: int


class InventoryRead(BaseModel):
    id: int
    sku: str
    name: str
    quantity: int
    reorder_point: int
    unit_cost: float

    model_config = {"from_attributes": True}
