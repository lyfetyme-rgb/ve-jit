"""Diebold Nixdorf adapter — Beetle terminal and supported peripheral integration.

Documented demo contract (replace with the Diebold Nixdorf integration guide
for the deployed Beetle generation):

  {"deviceEvent":"BEETLE_SALE","transactionId":..., "basket":[{sku,qty,price}], "total":...}
  {"deviceEvent":"BEETLE_ITEM_VOID","transactionId":..., "sku":..., "reason":...}
  {"deviceEvent":"BEETLE_HEARTBEAT","state":"READY|NOT_READY"}
"""

from typing import Any

from app.edge.adapters.base import AdapterContext, VendarixAdapter


class DieboldNixdorfAdapter(VendarixAdapter):
    vendor = "diebold-nixdorf"
    interface = "diebold-nixdorf/beetle-v1"

    def translate(self, raw: dict[str, Any], ctx: AdapterContext) -> list[dict]:
        event = str(raw.get("deviceEvent", "")).upper()

        if event == "BEETLE_SALE":
            txn = raw.get("transactionId")
            basket = raw.get("basket")
            if not txn or not isinstance(basket, list) or not basket:
                return self.unrecognized(raw, ctx, "BEETLE_SALE missing transactionId or basket")
            payload = {
                "transaction_id": txn,
                "lane_id": ctx.device_id,
                "lines": [
                    {"sku": item.get("sku"), "quantity": item.get("qty"), "unit_price": item.get("price")}
                    for item in basket
                ],
                "total": raw.get("total"),
            }
            return [ctx.manifest("pos.transaction.completed", payload, idempotency_key=f"beetle:{ctx.device_id}:{txn}")]

        if event == "BEETLE_ITEM_VOID":
            if not raw.get("transactionId"):
                return self.unrecognized(raw, ctx, "BEETLE_ITEM_VOID missing transactionId")
            return [ctx.manifest(
                "pos.void.observed",
                {"transaction_id": raw["transactionId"], "sku": raw.get("sku"), "reason": raw.get("reason")},
            )]

        if event == "BEETLE_HEARTBEAT":
            return [ctx.manifest("edge.device.heartbeat", {"terminal_status": raw.get("state", "UNKNOWN")})]

        return self.unrecognized(raw, ctx, f"unknown deviceEvent {event!r}")
