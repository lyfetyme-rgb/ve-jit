from fastapi import APIRouter
from app.core.jit_engine import JITEngine
from app.core.kafka_service import KafkaEventPublisher
from app.config import settings

router = APIRouter(prefix="/api/v1/jit", tags=["jit"])
engine = JITEngine()
publisher = KafkaEventPublisher()


@router.get("/demo")
def jit_demo():
    decision = engine.evaluate(
        sku="DAIRY-001",
        current_stock=45,
        annual_demand=4562,
        unit_cost=2.50,
        holding_cost_rate=0.25,
        demand_rate=2.5,
        lead_time_days=2,
        safety_stock=10,
    )
    return decision


@router.post("/emit-demo-event")
def emit_demo_event():
    payload = {"event": "queue_alert", "store_id": settings.STORE_ID}
    result = publisher.publish(settings.KAFKA_TOPIC_QUEUE_ALERTS, payload)
    return result
