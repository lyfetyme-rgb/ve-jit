from fastapi import APIRouter, Depends

from app.core.iot_integration import IoTIntegrationService
from app.pci_guard import forbid_cardholder_data_body

router = APIRouter(prefix="/api/v1/iot", tags=["iot"])
service = IoTIntegrationService()


@router.post("/normalize")
def normalize(payload: dict, _pci: None = Depends(forbid_cardholder_data_body("iot.normalize"))):
    return service.normalize_scan_event(payload)
