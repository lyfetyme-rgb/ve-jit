"""NCR Voyix adapter — POS and peripheral integration through supported interfaces.

Documented demo contract (replace with the vendor interface control document
when supplied by the banner):

  TX_COMPLETE        {"msgType":"TX_COMPLETE","txnId":..., "lines":[{sku,qty,price,ext}], "total":...}
  PRICE_QUERY        {"msgType":"PRICE_QUERY","sku":..., "laneId":...}
  VOID               {"msgType":"VOID","txnId":..., "voidReason":...}
  HEARTBEAT          {"msgType":"HEARTBEAT","terminalStatus":"READY|PAPER_LOW|OFFLINE"}
"""

from typing import Any

from app.edge.adapters.base import AdapterContext, VendarixAdapter


class NCRVoyixAdapter(VendarixAdapter):
    vendor = "ncr-voyix"
    interface = "ncr-voyix/pos-v1"

    def translate(self, raw: dict[str, Any], ctx: AdapterContext) -> list[dict]:
        msg = str(raw.get("msgType", "")).upper()
        if msg == "TX_COMPLETE":
            txn = raw.get("txnId")
            if not txn:
                return self.unrecognized(raw, ctx, "TX_COMPLETE without txnId")
            lines = raw.get("lines") or []
            if not isinstance(lines, list) or not lines:
                return self.unrecognized(raw, ctx, "TX_COMPLETE with no lines")
            payload = {
                "transaction_id": txn,
                "lane_id": raw.get("laneId") or ctx.device_id,
                "lines": [
                    {
                        "sku": line.get("sku"),
                        "quantity": line.get("qty"),
                        "unit_price": line.get("price"),
                        "extended": line.get("ext"),
                    }
                    for line in lines
                ],
                "total": raw.get("total"),
                "tender": raw.get("tender"),
            }
            return [ctx.manifest("pos.transaction.completed", payload, idempotency_key=f"ncr:{ctx.device_id}:{txn}")]
        if msg == "PRICE_QUERY":
            if not raw.get("sku"):
                return self.unrecognized(raw, ctx, "PRICE_QUERY without sku")
            return [ctx.manifest("pos.price_lookup", {"sku": raw["sku"], "lane_id": raw.get("laneId") or ctx.device_id})]
        if msg == "VOID":
            if not raw.get("txnId"):
                return self.unrecognized(raw, ctx, "VOID without txnId")
            return [ctx.manifest("pos.void.observed", {"transaction_id": raw["txnId"], "reason": raw.get("voidReason")})]
        if msg == "HEARTBEAT":
            return [ctx.manifest("edge.device.heartbeat", {"terminal_status": raw.get("terminalStatus", "UNKNOWN")})]
        return self.unrecognized(raw, ctx, f"unknown msgType {msg!r}")
