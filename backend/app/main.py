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
from app.planes import models as plane_models  # noqa: F401  (register plane tables with create_all)
from app.edge import models as edge_models  # noqa: F401  (register edge tables with create_all)
from app.cloud import models as cloud_models  # noqa: F401  (register cloud reconciliation tables)
from app.edge.integrity import register as register_append_only

register_append_only()  # event store and audit ledger are INSERT-only
from app.api.health import router as health_router
from app.api.inventory import router as inventory_router
from app.api.queue import router as queue_router
from app.api.iot import router as iot_router
from app.api.analytics import router as analytics_router
from app.api.jit import router as jit_router
from app.api.auth import router as auth_router
from app.api.network_intelligence import router as network_intelligence_router
from app.api.compliance import router as compliance_router
from app.api.planes import router as planes_router
from app.api.commerce import router as commerce_router
from app.api.operations import router as operations_router
from app.api.intelligence import router as intelligence_router
from app.api.edge import router as edge_router
from app.api.cloud import router as cloud_router
from app.api.assurance import router as assurance_router
from app.integrations import models as integration_models  # noqa: F401
from app.integrations.release_loop import router as release_loop_router
from app.integrations.self_healing import router as self_healing_router
from app.integrations.outbox_router import router as outbox_router
from app.cloud.replay_router import router as cloud_replay_router
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

    from app.db.database import ensure_version_columns
    ensure_version_columns()

    # Deterministic edge policies must exist before any event is evaluated.
    from app.db.database import SessionLocal
    from app.edge.policy import seed_default_policies

    with SessionLocal() as seed_db:
        created = seed_default_policies(seed_db)
        logger.info("Edge policy seed: %d default rule(s) created", created)

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
app.include_router(planes_router)
app.include_router(commerce_router)
app.include_router(operations_router)
app.include_router(intelligence_router)
app.include_router(edge_router)
app.include_router(cloud_router)
app.include_router(cloud_replay_router)
app.include_router(assurance_router)
app.include_router(release_loop_router)
app.include_router(self_healing_router)
app.include_router(outbox_router)

frontend_candidates = [
    Path(__file__).resolve().parents[2] / "frontend",  # repo layout: backend/app/main.py -> repo root
    Path("/app/frontend"),                             # container layout: compose mounts ./frontend here
]
frontend_dir = next((candidate for candidate in frontend_candidates if candidate.exists()), None)
if frontend_dir is not None:
    app.mount("/ui", StaticFiles(directory=str(frontend_dir), html=True), name="ui")


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/ui/")
