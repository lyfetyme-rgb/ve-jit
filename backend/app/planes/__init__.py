"""Three planes over one trusted data layer.

VE-JIT splits responsibility the same way the platform design specifies:

| plane | owns | primary responsibility |
|---|---|---|
| commerce | customer transactions | customer-facing commerce activity |
| operations | physical business processes | inventory, purchasing, receiving, replenishment |
| intelligence | analytical decisions | forecasting, recommendations, anomaly detection |

The governing rule (non-negotiable):

    Intelligence produces recommendations.
    Operations executes authorized business actions.
    Commerce remains responsible for the customer-facing transaction.

Concretely in this codebase:

- `app.planes.intelligence` never mutates stock, orders, prices or POs. It
  computes analyses and writes `Recommendation` rows (plane="intelligence").
- `app.planes.operations` executes business actions (stock movements, POs,
  receiving, transfers, adjustments, waste). Actions derived from an
  Intelligence `Recommendation` require it to be APPROVED first and are
  rejected with 409 otherwise. Manual actions require an explicit, non-empty
  actor.
- `app.planes.commerce` owns the customer-facing transaction end to end:
  pricing, promotions, loyalty, orders and transactions (POS and eCommerce).
"""

PLANE_IDS = ("commerce", "operations", "intelligence")

PLANES = {
    "commerce": {
        "id": "commerce",
        "name": "Commerce",
        "owns": "customer transactions",
        "primary_responsibility": "Customer-facing commerce activity",
        "capabilities": [
            "POS integration",
            "Orders and transactions",
            "Pricing and promotions",
            "Loyalty",
            "eCommerce",
            "Customer-facing workflows",
        ],
        "endpoint_prefix": "/api/v1/commerce",
        "executes_business_actions": True,
        "produces_recommendations": False,
        "owns_customer_transaction": True,
        "responsibilities": [
            "POS integration: ingest point-of-sale transactions (channel='pos')",
            "Orders and transactions: order lifecycle, receipts, cancellation",
            "Pricing and promotions: price book with single-best promotion evaluation",
            "Loyalty: member enrolment, point accrual on paid orders, tiering",
            "eCommerce: online order channel (channel='ecommerce') on the same transaction rail",
            "Customer-facing workflows: receipts and self-service cancellation",
        ],
    },
    "operations": {
        "id": "operations",
        "name": "Operations",
        "owns": "physical business processes",
        "primary_responsibility": "Inventory, purchasing, receiving, replenishment",
        "capabilities": [
            "Inventory ledger",
            "Purchasing and procurement",
            "Replenishment",
            "Receiving and put-away",
            "Distribution-center transfers",
            "Supplier terms and SLAs",
            "Vendor collaboration",
        ],
        "endpoint_prefix": "/api/v1/operations",
        "executes_business_actions": True,
        "produces_recommendations": False,
        "owns_customer_transaction": False,
        "responsibilities": [
            "Inventory ledger: append-only stock movement journal behind every quantity change",
            "Purchasing and procurement: purchase orders with creator != approver segregation",
            "Replenishment: executes approved Intelligence replenishment recommendations",
            "Receiving and put-away: PO receipt against ordered lines, restocks inventory",
            "Distribution-center transfers: REQUESTED -> SHIPPED -> RECEIVED with ledger postings",
            "Supplier terms and SLAs: payment terms, lead time, OTIF and fill-rate SLA targets",
            "Vendor collaboration: supplier-directed messages linked to suppliers and POs",
        ],
    },
    "intelligence": {
        "id": "intelligence",
        "name": "Intelligence",
        "owns": "analytical decisions",
        "primary_responsibility": "Forecasting, recommendations, anomaly detection",
        "capabilities": [
            "Demand forecasting",
            "Inventory anomaly detection",
            "Shelf availability insights",
            "Queue and workforce guidance",
            "Supplier intelligence",
            "Waste and spoilage analysis",
        ],
        "endpoint_prefix": "/api/v1/intelligence",
        "executes_business_actions": False,
        "produces_recommendations": True,
        "owns_customer_transaction": False,
        "responsibilities": [
            "Demand forecasting: SES/SMA forecasts from supplied history or trailing sales",
            "Inventory anomaly detection: stockout risk, overstock and dead-stock watch",
            "Shelf availability insights: days-of-supply and availability banding per SKU",
            "Queue and workforce guidance: lanes-to-open guidance (recommendation only)",
            "Supplier intelligence: fill-rate / on-time scorecards vs supplier SLA terms",
            "Waste and spoilage analysis: waste aggregation by reason and SKU (analysis only)",
        ],
    },
}


def plane(plane_id: str):
    return PLANES.get(plane_id)
