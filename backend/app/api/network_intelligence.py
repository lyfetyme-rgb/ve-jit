from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import APIRouter

from app.core.network_intelligence import (
    AnonymousPerformanceData,
    GeographicRegion,
    NetworkIntelligence,
    StoreProfile,
    StoreType,
    VendorNetwork,
)

router = APIRouter(prefix="/api/v1/network", tags=["network-intelligence"])

network = NetworkIntelligence(
    network_api_url="https://network.vejit.example/api",
    api_key="demo_key",
)
vendor_network = VendorNetwork(
    network_api_url="https://network.vejit.example/api",
    api_key="demo_key",
)


def demo_store() -> StoreProfile:
    return StoreProfile(
        store_uuid=uuid4(),
        store_type=StoreType.GROCERY_MEDIUM,
        region=GeographicRegion.NORTHEAST,
        avg_daily_customers=1200,
        avg_transaction_value=47.50,
        num_lanes=6,
        operating_hours_per_day=16,
        has_self_checkout=True,
        has_pharmacy=False,
        has_deli=True,
        square_footage=25000,
        median_household_income=72000,
        population_density="suburban",
    )


@router.get("/insights")
async def get_insights():
    store = demo_store()
    insights = await network.get_network_insights(store)
    return [
        {
            "insight_type": i.insight_type,
            "title": i.title,
            "description": i.description,
            "confidence": i.confidence,
            "sample_size": i.sample_size,
            "recommended_action": i.recommended_action,
            "expected_impact": i.expected_impact,
            "success_rate": i.success_rate,
        }
        for i in insights
    ]


@router.get("/percentile")
async def get_percentile():
    store = demo_store()
    perf = AnonymousPerformanceData(
        store_uuid=store.store_uuid,
        report_date=datetime.now(timezone.utc),
        avg_wait_time_seconds=165.0,
        max_wait_time_seconds=285.0,
        total_customers_served=1247,
        queue_alerts_triggered=8,
        avg_abandonment_rate=0.03,
        stockout_events=2,
        overstock_events=1,
        inventory_turnover_ratio=8.2,
        waste_percentage=2.1,
        labor_hours_used=128.0,
        revenue_per_labor_hour=485.50,
        transactions_per_hour=9.7,
        ai_recommendations_generated=24,
        ai_recommendations_followed=21,
        alert_response_time_avg_seconds=145.0,
    )
    return await network.get_performance_percentile(store, perf, "avg_wait_time_seconds")


@router.get("/demand-spike")
async def demand_spike():
    store = demo_store()
    now = datetime.now(timezone.utc)
    return await network.predict_demand_spike(store, (now, now + timedelta(days=7)))


@router.get("/vendors")
async def find_vendors():
    return await vendor_network.find_vendors("produce", GeographicRegion.NORTHEAST)


@router.get("/aggregate-order")
async def aggregate_order():
    return await vendor_network.aggregate_order("vendor_001", "produce_001", 100, uuid4())
