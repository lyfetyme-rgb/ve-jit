from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.db.models import InventoryItem
from app.schemas.inventory import InventoryCreate, InventoryRead, InventoryUpdate

router = APIRouter(prefix="/api/v1/inventory", tags=["inventory"])


@router.get("", response_model=list[InventoryRead])
def list_inventory(db: Session = Depends(get_db)):
    return db.query(InventoryItem).order_by(InventoryItem.sku.asc()).all()


@router.post("", response_model=InventoryRead)
def create_inventory(payload: InventoryCreate, db: Session = Depends(get_db)):
    existing = db.query(InventoryItem).filter(InventoryItem.sku == payload.sku).first()
    if existing:
        raise HTTPException(status_code=409, detail="SKU already exists")
    item = InventoryItem(**payload.model_dump())
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@router.patch("/{sku}", response_model=InventoryRead)
def update_inventory(sku: str, payload: InventoryUpdate, db: Session = Depends(get_db)):
    item = db.query(InventoryItem).filter(InventoryItem.sku == sku).first()
    if not item:
        raise HTTPException(status_code=404, detail="SKU not found")
    item.quantity += payload.quantity_delta
    db.commit()
    db.refresh(item)
    return item
