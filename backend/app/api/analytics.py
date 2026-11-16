from fastapi import APIRouter
from app.core.analytics import AnalyticsEngine

router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])
engine = AnalyticsEngine()


@router.post("/summary")
def summary(payload: dict):
    values = payload.get("values", [])
    return engine.summarize_sales(values)
