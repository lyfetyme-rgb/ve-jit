import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import List

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.responses import RedirectResponse

from app.config import settings
from app.db.database import Base, engine
from app.api.health import router as health_router
from app.api.inventory import router as inventory_router
from app.api.queue import router as queue_router
from app.api.iot import router as iot_router
from app.api.analytics import router as analytics_router
from app.api.jit import router as jit_router
from app.api.auth import router as auth_router
from app.api.network_intelligence import router as network_intelligence_router
from app.api.compliance import router as compliance_router
from app.middleware.audit import AuditLogMiddleware
from app.core.queue_management_ai import QueueDataProvider, QueueManagementAI, QueueMetrics, QueueMonitor

logger = logging.getLogger(__name__)


class DemoQueueDataProvider(QueueDataProvider):
    async def get_queue_metrics(self, store_id: str) -> List[QueueMetrics]:
        now = datetime.now(timezone.utc)
        return [
            QueueMetrics(
                lane_id="lane_1",
                customers_in_line=7,
                average_wait_time=360.0,
                items_per_cart=20.0,
                cashier_speed=18.0,
                estimated_wait_new_customer=400.0,
                timestamp=now,
            ),
            QueueMetrics(
                lane_id="lane_2",
                customers_in_line=9,
                average_wait_time=420.0,
                items_per_cart=22.0,
                cashier_speed=19.0,
                estimated_wait_new_customer=460.0,
                timestamp=now,
            ),
        ]


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)

    ai_engine = QueueManagementAI(
        target_wait_seconds=settings.QUEUE_TARGET_WAIT_SECONDS,
        max_wait_seconds=settings.QUEUE_MAX_WAIT_SECONDS,
        alert_cooldown_seconds=settings.QUEUE_ALERT_COOLDOWN_SECONDS,
        avg_order_value=settings.QUEUE_AVG_ORDER_VALUE,
        safety_buffer=settings.QUEUE_SAFETY_BUFFER,
    )
    provider = DemoQueueDataProvider()
    queue_monitor = QueueMonitor(ai_engine, provider, poll_interval_seconds=settings.QUEUE_POLL_INTERVAL_SECONDS)
    task = asyncio.create_task(queue_monitor.monitor_queues(settings.STORE_ID))

    app.state.queue_monitor = queue_monitor
    app.state.queue_monitor_task = task
    logger.info("Queue monitor startup complete")

    try:
        yield
    finally:
        queue_monitor.stop()
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        logger.info("Queue monitor shutdown complete")


app = FastAPI(title=settings.APP_NAME, version=settings.APP_VERSION, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(AuditLogMiddleware)

app.include_router(health_router)
app.include_router(inventory_router)
app.include_router(queue_router)
app.include_router(iot_router)
app.include_router(analytics_router)
app.include_router(jit_router)
app.include_router(auth_router)
app.include_router(network_intelligence_router)
app.include_router(compliance_router)

frontend_dir = Path(__file__).resolve().parents[2] / "frontend"
if frontend_dir.exists():
    app.mount("/ui", StaticFiles(directory=str(frontend_dir), html=True), name="ui")


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/ui/")
