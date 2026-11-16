from fastapi import APIRouter
from app.core.iot_integration import IoTIntegrationService

router = APIRouter(prefix="/api/v1/iot", tags=["iot"])
service = IoTIntegrationService()


@router.post("/normalize")
def normalize(payload: dict):
    return service.normalize_scan_event(payload)
