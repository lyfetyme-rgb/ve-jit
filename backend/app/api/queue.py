from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.core.queue_management_ai import QueueDataProvider, QueueManagementAI, QueueMetrics, QueueMonitor

router = APIRouter(prefix="/api/v1/queue", tags=["queue"])


class FallbackProvider(QueueDataProvider):
    async def get_queue_metrics(self, store_id: str):
        now = datetime.now(timezone.utc)
        return [
            QueueMetrics(
                lane_id="lane_1",
                customers_in_line=6,
                average_wait_time=220.0,
                items_per_cart=18.0,
                cashier_speed=20.0,
                estimated_wait_new_customer=250.0,
                timestamp=now,
            ),
            QueueMetrics(
                lane_id="lane_2",
                customers_in_line=5,
                average_wait_time=180.0,
                items_per_cart=16.0,
                cashier_speed=19.0,
                estimated_wait_new_customer=210.0,
                timestamp=now,
            ),
        ]


async def _fallback_status(store_id: str):
    ai = QueueManagementAI()
    provider = FallbackProvider()
    metrics = await provider.get_queue_metrics(store_id)
    return ai.analyze_queue_state(metrics)


@router.get("/recommendation")
async def get_recommendation(request: Request):
    queue_monitor = getattr(request.app.state, "queue_monitor", None)

    if queue_monitor is None:
        status = await _fallback_status("store_001")
    else:
        status = await queue_monitor.get_current_status("store_001")
        if status is None:
            status = await _fallback_status("store_001")

    return {
        "store_id": "store_001",
        "urgency": status.urgency.value,
        "action": status.action.value,
        "recommended_lanes": status.recommended_lanes,
        "recommended_registers": status.recommended_lanes,
        "current_lanes": status.current_lanes,
        "reasoning": status.reasoning,
        "estimated_wait_reduction": status.estimated_wait_reduction,
        "revenue_at_risk": status.revenue_at_risk,
    }


@router.get("/status/{store_id}")
async def queue_status(store_id: str, request: Request):
    queue_monitor = getattr(request.app.state, "queue_monitor", None)

    if queue_monitor is None:
        status = await _fallback_status(store_id)
    else:
        status = await queue_monitor.get_current_status(store_id)
        if status is None:
            status = await _fallback_status(store_id)

    return {
        "store_id": store_id,
        "urgency": status.urgency.value,
        "action": status.action.value,
        "recommended_lanes": status.recommended_lanes,
        "recommended_registers": status.recommended_lanes,
        "current_lanes": status.current_lanes,
        "reasoning": status.reasoning,
        "estimated_wait_reduction": status.estimated_wait_reduction,
        "revenue_at_risk": status.revenue_at_risk,
    }
