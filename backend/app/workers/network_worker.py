import asyncio
import logging
from datetime import datetime, timezone
from uuid import uuid4

from app.core.network_intelligence import (
    AnonymousPerformanceData,
    GeographicRegion,
    NetworkIntelligence,
    StoreProfile,
    StoreType,
)

logger = logging.getLogger(__name__)


async def main():
    network = NetworkIntelligence("https://network.vejit.example/api", "demo_key")
    store = StoreProfile(
        store_uuid=uuid4(),
        store_type=StoreType.GROCERY_MEDIUM,
        region=GeographicRegion.NORTHEAST,
        avg_daily_customers=1200,
        avg_transaction_value=47.5,
        num_lanes=6,
        operating_hours_per_day=16,
        has_self_checkout=True,
        has_pharmacy=False,
        has_deli=True,
        square_footage=25000,
        median_household_income=72000,
        population_density="suburban",
    )
    logger.info("Network worker started")
    while True:
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
        await network.share_performance_data(store, perf)
        await asyncio.sleep(60)


if __name__ == "__main__":
    asyncio.run(main())
