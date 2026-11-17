"""Intelligence plane API.

Intelligence produces recommendations; it never executes business actions.
Every endpoint here is either a pure computation or a read against the
trusted layer. Persisting analysis as a PROPOSED recommendation is allowed;
turning a recommendation into action is Operations' job and lives behind
/api/v1/operations/recommendations/{uid}/execute.
"""

import json
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import settings
from app.core.queue_management_ai import QueueManagementAI, QueueMetrics
from app.db.database import get_db
from app.planes.governance import (
    RecommendationError,
    approve_recommendation,
    create_recommendation,
    get_recommendation,
    recommendation_to_dict,
    reject_recommendation,
)
from app.planes.intelligence import (
    detect_anomalies,
    forecast_demand,
    sales_history,
    shelf_availability,
    supplier_scorecard,
    waste_summary,
)
from app.planes.models import Recommendation

router = APIRouter(prefix="/api/v1/intelligence", tags=["intelligence"])


# ------------------------------------------------------------------ schemas

class ForecastRequest(BaseModel):
    sku: Optional[str] = None
    history: Optional[List[float]] = None
    horizon: int = Field(7, ge=1, le=365)
    method: str = Field("ses", pattern="^(ses|sma)$")
    alpha: float = Field(0.4, gt=0, lt=1)
    window: int = Field(7, ge=1, le=365)
    history_days: int = Field(28, ge=7, le=365)


class RecommendationCreate(BaseModel):
    kind: str = Field(..., min_length=1, max_length=64)
    subject_type: str = Field(..., min_length=1, max_length=64)
    subject_id: str = Field(..., min_length=1, max_length=128)
    summary: str = Field(..., min_length=1, max_length=512)
    rationale: Optional[str] = None
    confidence: float = Field(0.5, ge=0.0, le=1.0)
    recommended_action: Optional[dict] = None
    created_by: str = "manual-analysis"


class RecommendationApprove(BaseModel):
    approver: str = Field(..., min_length=1)
    note: Optional[str] = None


class RecommendationReject(BaseModel):
    actor: str = Field(..., min_length=1)
    note: Optional[str] = None


# --------------------------------------------------------------- forecast

@router.post("/forecast/demand")
def demand_forecast(payload: ForecastRequest, db: Session = Depends(get_db)):
    if payload.history is not None:
        history = payload.history
        source = "request"
    elif payload.sku:
        history = sales_history(db, payload.sku, days=payload.history_days)
        source = f"sales_history({payload.history_days}d)"
    else:
        raise HTTPException(status_code=422, detail="Provide either history or sku")

    try:
        result = forecast_demand(history, horizon=payload.horizon, method=payload.method, alpha=payload.alpha, window=payload.window)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    result["sku"] = payload.sku
    result["source"] = source
    return result


# --------------------------------------------------------------- anomalies

@router.get("/anomalies")
def inventory_anomalies(
    store_id: Optional[str] = None,
    create_recommendations: bool = False,
    db: Session = Depends(get_db),
):
    return detect_anomalies(db, store_id or settings.STORE_ID, create_recommendations=create_recommendations)


# ----------------------------------------------------- shelf availability

@router.get("/shelf-availability")
def shelf_availability_insights(store_id: Optional[str] = None, db: Session = Depends(get_db)):
    return shelf_availability(db, store_id or settings.STORE_ID)


# ------------------------------------------------- workforce guidance

@router.get("/workforce-guidance")
async def workforce_guidance(store_id: Optional[str] = None):
    """Queue and workforce guidance as a recommendation (never an instruction)."""
    from app.api.queue import FallbackProvider

    ai = QueueManagementAI(
        target_wait_seconds=settings.QUEUE_TARGET_WAIT_SECONDS,
        max_wait_seconds=settings.QUEUE_MAX_WAIT_SECONDS,
        alert_cooldown_seconds=settings.QUEUE_ALERT_COOLDOWN_SECONDS,
        avg_order_value=settings.QUEUE_AVG_ORDER_VALUE,
        safety_buffer=settings.QUEUE_SAFETY_BUFFER,
    )
    metrics = await FallbackProvider().get_queue_metrics(store_id or settings.STORE_ID)
    status = ai.analyze_queue_state(metrics)
    return {
        "plane": "intelligence",
        "kind": "WORKFORCE_GUIDANCE",
        "status": "PROPOSED",
        "store_id": store_id or settings.STORE_ID,
        "summary": f"{status.action.value}: open lanes {status.recommended_lanes} (current {status.current_lanes})",
        "urgency": status.urgency.value,
        "recommended_lanes": status.recommended_lanes,
        "reasoning": status.reasoning,
        "estimated_wait_reduction": status.estimated_wait_reduction,
        "revenue_at_risk": status.revenue_at_risk,
        "executes_actions": False,
        "note": "Guidance only. Opening lanes or assigning staff is an Operations/HITL decision, not an Intelligence action.",
    }


# -------------------------------------------------------- supplier scorecard

@router.get("/suppliers/{supplier_id}/scorecard")
def supplier_scorecard_endpoint(supplier_id: int, db: Session = Depends(get_db)):
    try:
        return supplier_scorecard(db, supplier_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


# ------------------------------------------------------------------- waste

@router.get("/waste/summary")
def waste_analysis(store_id: Optional[str] = None, days: int = Query(30, ge=1, le=365), db: Session = Depends(get_db)):
    return waste_summary(db, store_id or settings.STORE_ID, days=days)


# -------------------------------------------------------- recommendations

@router.get("/recommendations")
def list_recommendations(
    status: Optional[str] = None,
    kind: Optional[str] = None,
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    query = db.query(Recommendation)
    if status:
        query = query.filter(Recommendation.status == status.upper())
    if kind:
        query = query.filter(Recommendation.kind == kind.upper())
    rows = query.order_by(Recommendation.id.desc()).limit(limit).all()
    return {"recommendations": [recommendation_to_dict(r) for r in rows]}


@router.post("/recommendations", status_code=201)
def create_manual_recommendation(payload: RecommendationCreate, db: Session = Depends(get_db)):
    rec = create_recommendation(
        db,
        kind=payload.kind.upper(),
        subject_type=payload.subject_type.lower(),
        subject_id=payload.subject_id,
        summary=payload.summary,
        rationale=payload.rationale,
        confidence=payload.confidence,
        recommended_action=json.dumps(payload.recommended_action) if payload.recommended_action is not None else None,
        created_by=payload.created_by,
    )
    return recommendation_to_dict(rec)


@router.get("/recommendations/{uid}")
def get_recommendation_endpoint(uid: str, db: Session = Depends(get_db)):
    try:
        return recommendation_to_dict(get_recommendation(db, uid))
    except RecommendationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)


@router.post("/recommendations/{uid}/approve")
def approve(uid: str, payload: RecommendationApprove, db: Session = Depends(get_db)):
    try:
        rec = approve_recommendation(db, uid, approver=payload.approver, note=payload.note)
    except RecommendationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    return recommendation_to_dict(rec)


@router.post("/recommendations/{uid}/reject")
def reject(uid: str, payload: RecommendationReject, db: Session = Depends(get_db)):
    try:
        rec = reject_recommendation(db, uid, actor=payload.actor, note=payload.note)
    except RecommendationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    return recommendation_to_dict(rec)


# ------------------------------------------------------------------ summary

@router.get("/summary")
def intelligence_summary(store_id: Optional[str] = None, db: Session = Depends(get_db)):
    store = store_id or settings.STORE_ID
    counts = {
        status: db.query(Recommendation).filter(Recommendation.status == status).count()
        for status in ("PROPOSED", "APPROVED", "REJECTED", "EXECUTED")
    }
    anomalies = detect_anomalies(db, store, create_recommendations=False)
    waste = waste_summary(db, store, days=30)
    return {
        "store_id": store,
        "recommendations": counts,
        "anomaly_count": len(anomalies["anomalies"]),
        "waste_30d_cost": waste["total_estimated_cost"],
        "note": "Intelligence produces recommendations. Operations executes authorized business actions.",
    }
