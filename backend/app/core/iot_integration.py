from typing import Dict, Any


class IoTIntegrationService:
    def normalize_scan_event(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "device_id": payload.get("device_id", "unknown"),
            "sku": payload.get("sku"),
            "quantity": int(payload.get("quantity", 1)),
            "store_id": payload.get("store_id", "store_001"),
            "timestamp": payload.get("timestamp"),
        }
