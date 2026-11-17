"""Enterprise event adapter — ERP / WMS / supplier events arriving on the
fabric. The event type is declared by the producer; it must be a known
Vendarix event type, otherwise the payload is quarantined as unrecognized.
"""

from typing import Any

from app.edge.adapters.base import AdapterContext, VendarixAdapter
from app.edge.contracts import EVENT_TYPES


class EnterpriseAdapter(VendarixAdapter):
    vendor = "enterprise"
    interface = "enterprise/erp-wms-supplier-v1"

    def translate(self, raw: dict[str, Any], ctx: AdapterContext) -> list[dict]:
        event_type = str(raw.get("event_type") or raw.get("eventType") or "").lower()
        if event_type not in EVENT_TYPES or not (
            event_type.startswith("erp.")
            or event_type.startswith("wms.")
            or event_type.startswith("supplier.")
            or event_type.startswith("inventory.")
        ):
            return self.unrecognized(raw, ctx, f"unsupported enterprise event_type {event_type!r}")
        inner = raw.get("payload") if isinstance(raw.get("payload"), dict) else {k: v for k, v in raw.items() if k not in ("event_type", "eventType")}
        return [ctx.manifest(event_type, inner)]
