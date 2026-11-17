"""Toshiba Global Commerce Solutions adapter — legacy IBM/Toshiba integration
and supported JavaPOS/UPOS interfaces.

Documented demo contracts:

  UPOS (Toshiba TCx series, JavaPOS 1.14)
    {"eventType":"UPOS.TRANSACTION_COMPLETE","transactionId":..., "items":[{sku,qty,price}], "total":...}
    {"eventType":"UPOS.VOID_TRANSACTION","transactionId":..., "reason":...}
  JavaPOS legacy
    {"eventType":"JPOS.ITEM_SOLD","receiptSerial":..., "item":{sku,qty,price}}
"""

from typing import Any

from app.edge.adapters.base import AdapterContext, VendarixAdapter
from app.edge.contracts import new_event_id


class ToshibaTCGSAdapter(VendarixAdapter):
    vendor = "toshiba-tcgs"
    interface = "toshiba-tcgs/upos-javapos-v1"

    def translate(self, raw: dict[str, Any], ctx: AdapterContext) -> list[dict]:
        event = str(raw.get("eventType", "")).upper()

        if event == "UPOS.TRANSACTION_COMPLETE":
            txn = raw.get("transactionId")
            items = raw.get("items")
            if not txn or not isinstance(items, list) or not items:
                return self.unrecognized(raw, ctx, "UPOS.TRANSACTION_COMPLETE missing transactionId or items")
            payload = {
                "transaction_id": txn,
                "lane_id": ctx.device_id,
                "lines": [
                    {"sku": item.get("sku"), "quantity": item.get("qty"), "unit_price": item.get("price")}
                    for item in items
                ],
                "total": raw.get("total"),
                "interface": "UPOS",
            }
            return [ctx.manifest("pos.transaction.completed", payload, idempotency_key=f"upos:{ctx.device_id}:{txn}")]

        if event == "UPOS.VOID_TRANSACTION":
            if not raw.get("transactionId"):
                return self.unrecognized(raw, ctx, "UPOS.VOID_TRANSACTION missing transactionId")
            return [ctx.manifest("pos.void.observed", {"transaction_id": raw["transactionId"], "reason": raw.get("reason")})]

        if event == "JPOS.ITEM_SOLD":
            item = raw.get("item") or {}
            sku = item.get("sku") or raw.get("sku")
            if not sku:
                return self.unrecognized(raw, ctx, "JPOS.ITEM_SOLD missing sku")
            # Legacy events carry no stable id; receiptSerial deduplicates when present,
            # otherwise a random key is generated (replay dedup is best-effort for this shape).
            serial = raw.get("receiptSerial") or new_event_id()
            payload = {
                "transaction_id": serial,
                "lane_id": ctx.device_id,
                "lines": [{"sku": sku, "quantity": item.get("qty", 1), "unit_price": item.get("price")}],
                "total": None,
                "interface": "JAVAPOS",
            }
            return [ctx.manifest("pos.transaction.completed", payload, idempotency_key=f"jpos:{ctx.device_id}:{serial}")]

        return self.unrecognized(raw, ctx, f"unknown eventType {event!r}")
