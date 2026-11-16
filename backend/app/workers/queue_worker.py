import asyncio
import logging
from datetime import datetime, timezone

from app.config import settings
from app.core.queue_management_ai import QueueDataProvider, QueueManagementAI, QueueMetrics, QueueMonitor

logger = logging.getLogger(__name__)


class DemoQueueDataProvider(QueueDataProvider):
    async def get_queue_metrics(self, store_id: str):
        now = datetime.now(timezone.utc)
        return [
            QueueMetrics(
                lane_id="lane_1",
                customers_in_line=6,
                average_wait_time=300.0,
                items_per_cart=18.0,
                cashier_speed=20.0,
                estimated_wait_new_customer=320.0,
                timestamp=now,
            )
        ]


async def main():
    monitor = QueueMonitor(
        QueueManagementAI(
            target_wait_seconds=settings.QUEUE_TARGET_WAIT_SECONDS,
            max_wait_seconds=settings.QUEUE_MAX_WAIT_SECONDS,
            alert_cooldown_seconds=settings.QUEUE_ALERT_COOLDOWN_SECONDS,
            avg_order_value=settings.QUEUE_AVG_ORDER_VALUE,
            safety_buffer=settings.QUEUE_SAFETY_BUFFER,
        ),
        DemoQueueDataProvider(),
        poll_interval_seconds=settings.QUEUE_POLL_INTERVAL_SECONDS,
    )
    logger.info("Queue worker started")
    await monitor.monitor_queues(settings.STORE_ID)


if __name__ == "__main__":
    asyncio.run(main())
