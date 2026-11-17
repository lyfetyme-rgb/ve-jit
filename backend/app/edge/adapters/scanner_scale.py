"""Scanner-scale adapter — bi-optic scanner, scale, weight, calibration and
label events.

Documented demo contract:

  {"event":"WEIGHT_STABLE","sku":..., "weightGrams":1250, "laneId":...}
  {"event":"ITEM_SCANNED","sku":..., "scanType":"UPC_A|EAN13|GS1_DATABAR"}
  {"event":"SCALE_CALIBRATED","referenceWeightGrams":2000, "deviationGrams":3}
  {"event":"LABEL_PRINTED","sku":..., "weightGrams":1250, "labelFormat":"PLU"}
"""

from typing import Any

from app.edge.adapters.base import AdapterContext, VendarixAdapter


class ScannerScaleAdapter(VendarixAdapter):
    vendor = "scanner-scale"
    interface = "scanner-scale/bi-optic-v1"

    def translate(self, raw: dict[str, Any], ctx: AdapterContext) -> list[dict]:
        event = str(raw.get("event", "")).upper()

        if event == "WEIGHT_STABLE":
            sku = raw.get("sku")
            weight = raw.get("weightGrams")
            if not sku or not isinstance(weight, (int, float)):
                return self.unrecognized(raw, ctx, "WEIGHT_STABLE missing sku or weightGrams")
            return [ctx.manifest("scanner.weight.measured", {
                "sku": sku,
                "weight_grams": weight,
                "lane_id": raw.get("laneId") or ctx.device_id,
                "tare_grams": raw.get("tareGrams", 0),
            })]

        if event == "ITEM_SCANNED":
            if not raw.get("sku"):
                return self.unrecognized(raw, ctx, "ITEM_SCANNED missing sku")
            return [ctx.manifest("scanner.item.scanned", {
                "sku": raw["sku"],
                "scan_type": raw.get("scanType", "UPC_A"),
                "lane_id": raw.get("laneId") or ctx.device_id,
            })]

        if event == "SCALE_CALIBRATED":
            return [ctx.manifest("scale.calibrated", {
                "reference_weight_grams": raw.get("referenceWeightGrams"),
                "deviation_grams": raw.get("deviationGrams"),
                "operator_id": raw.get("operatorId"),
            })]

        if event == "LABEL_PRINTED":
            if not raw.get("sku"):
                return self.unrecognized(raw, ctx, "LABEL_PRINTED missing sku")
            return [ctx.manifest("scale.label.printed", {
                "sku": raw["sku"],
                "weight_grams": raw.get("weightGrams"),
                "label_format": raw.get("labelFormat", "PLU"),
                "lane_id": ctx.device_id,
            })]

        return self.unrecognized(raw, ctx, f"unknown event {event!r}")
