from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

try:
    import numpy as np  # optional
except Exception:  # pragma: no cover
    np = None


class QueueUrgency(Enum):
    NORMAL = "NORMAL"
    ATTENTION = "ATTENTION"
    URGENT = "URGENT"
    CRITICAL = "CRITICAL"


class StaffingAction(Enum):
    OPEN_REGISTER = "open_register"
    MAINTAIN = "maintain"
    CLOSE_REGISTER = "close_register"


@dataclass(frozen=True)
class QueueMetrics:
    lane_id: str
    customers_in_line: int
    average_wait_time: float  # seconds
    items_per_cart: float     # average items per customer in this lane snapshot
    cashier_speed: float      # items per minute
    estimated_wait_new_customer: float  # seconds
    timestamp: datetime


@dataclass(frozen=True)
class StaffingRecommendation:
    urgency: QueueUrgency
    action: StaffingAction
    recommended_lanes: int
    current_lanes: int
    reasoning: str
    estimated_wait_reduction: float  # seconds
    revenue_at_risk: float           # dollars


class QueueManagementAI:
    """
    Queue staffing engine.

    Service rate is estimated from lane throughput:
        customers/minute ~= cashier_speed(items/min) / items_per_cart(items/customer)

    Arrival rate is estimated from backlog + service, not from service alone:
        lambda ~= mu_total + dQ/dt
    """

    def __init__(
        self,
        target_wait_seconds: int = 180,
        max_wait_seconds: int = 300,
        alert_cooldown_seconds: int = 300,
        avg_order_value: float = 45.0,
        safety_buffer: float = 1.15,
    ) -> None:
        self.target_wait_seconds = max(30, target_wait_seconds)
        self.max_wait_seconds = max(self.target_wait_seconds, max_wait_seconds)
        self.alert_cooldown = max(0, alert_cooldown_seconds)
        self.avg_order_value = max(0.0, avg_order_value)
        self.safety_buffer = max(1.0, safety_buffer)

        self.abandonment_rate_by_wait: Dict[float, float] = {
            0.0: 0.00,
            60.0: 0.01,
            120.0: 0.03,
            180.0: 0.05,
            240.0: 0.10,
            300.0: 0.18,
            420.0: 0.30,
            600.0: 0.45,
        }

        self._last_snapshot_at: Optional[datetime] = None
        self._last_total_customers: Optional[int] = None
        self._last_alert_at: Optional[datetime] = None

    def analyze_queue_state(
        self,
        queue_metrics: Sequence[QueueMetrics],
    ) -> StaffingRecommendation:
        if not queue_metrics:
            return StaffingRecommendation(
                urgency=QueueUrgency.NORMAL,
                action=StaffingAction.MAINTAIN,
                recommended_lanes=1,
                current_lanes=0,
                reasoning="No queue data available; sensors may be offline.",
                estimated_wait_reduction=0.0,
                revenue_at_risk=0.0,
            )

        valid_metrics = self._validate_metrics(queue_metrics)
        if not valid_metrics:
            return StaffingRecommendation(
                urgency=QueueUrgency.NORMAL,
                action=StaffingAction.MAINTAIN,
                recommended_lanes=1,
                current_lanes=len(queue_metrics),
                reasoning="Invalid queue data detected; check sensors or upstream feeds.",
                estimated_wait_reduction=0.0,
                revenue_at_risk=0.0,
            )

        current_lanes = len(valid_metrics)
        total_customers = sum(q.customers_in_line for q in valid_metrics)
        avg_wait_time = sum(q.average_wait_time for q in valid_metrics) / current_lanes
        max_wait_time = max(q.average_wait_time for q in valid_metrics)

        per_lane_service_rates = [
            self._lane_service_rate_customers_per_minute(q) for q in valid_metrics
        ]
        avg_service_rate = sum(per_lane_service_rates) / current_lanes
        total_service_rate = sum(per_lane_service_rates)

        arrival_rate = self._estimate_arrival_rate(valid_metrics, total_customers, total_service_rate)

        required_lanes = max(
            1,
            math.ceil(arrival_rate / max(avg_service_rate, 0.1) * self.safety_buffer),
        )

        if avg_wait_time > self.target_wait_seconds:
            wait_pressure = min(avg_wait_time / self.target_wait_seconds, 2.0)
            required_lanes = max(required_lanes, math.ceil(current_lanes * wait_pressure))

        if total_customers == 0 and avg_wait_time <= 30:
            required_lanes = max(1, min(required_lanes, current_lanes - 1)) if current_lanes > 1 else 1

        recommended_lanes = max(1, required_lanes)
        action = self._choose_action(current_lanes, recommended_lanes, avg_wait_time, total_customers)
        urgency = self._determine_urgency(avg_wait_time, max_wait_time, total_customers, current_lanes)
        revenue_at_risk = self._calculate_revenue_at_risk(total_customers, avg_wait_time, self.avg_order_value)
        estimated_wait_reduction = self._estimate_wait_reduction(
            current_lanes=current_lanes,
            recommended_lanes=recommended_lanes,
            avg_wait_time=avg_wait_time,
        )

        reasoning = self._build_reasoning(
            total_customers=total_customers,
            avg_wait_time=avg_wait_time,
            max_wait_time=max_wait_time,
            current_lanes=current_lanes,
            recommended_lanes=recommended_lanes,
            action=action,
        )

        self._update_snapshot(total_customers)

        return StaffingRecommendation(
            urgency=urgency,
            action=action,
            recommended_lanes=recommended_lanes,
            current_lanes=current_lanes,
            reasoning=reasoning,
            estimated_wait_reduction=estimated_wait_reduction,
            revenue_at_risk=revenue_at_risk,
        )

    def should_send_alert(self, recommendation: StaffingRecommendation, now: Optional[datetime] = None) -> bool:
        now = now or datetime.now(timezone.utc)

        if recommendation.urgency == QueueUrgency.NORMAL:
            return False

        if self._last_alert_at is None:
            self._last_alert_at = now
            return True

        if (now - self._last_alert_at).total_seconds() < self.alert_cooldown:
            return False

        self._last_alert_at = now
        return True

    def _validate_metrics(self, queue_metrics: Sequence[QueueMetrics]) -> List[QueueMetrics]:
        valid: List[QueueMetrics] = []
        for q in queue_metrics:
            if (
                isinstance(q.lane_id, str)
                and q.lane_id.strip()
                and q.customers_in_line >= 0
                and q.average_wait_time >= 0
                and q.items_per_cart >= 0
                and q.cashier_speed >= 0
                and q.estimated_wait_new_customer >= 0
                and isinstance(q.timestamp, datetime)
            ):
                valid.append(q)
        return valid

    def _lane_service_rate_customers_per_minute(self, q: QueueMetrics) -> float:
        items_per_cart = max(q.items_per_cart, 1.0)
        cashier_speed = max(q.cashier_speed, 0.1)
        return cashier_speed / items_per_cart

    def _estimate_arrival_rate(
        self,
        valid_metrics: Sequence[QueueMetrics],
        total_customers: int,
        total_service_rate: float,
    ) -> float:
        now = max(q.timestamp for q in valid_metrics)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)

        if self._last_snapshot_at and self._last_total_customers is not None:
            elapsed_min = max((now - self._last_snapshot_at).total_seconds() / 60.0, 1.0 / 60.0)
            queue_delta_per_min = (total_customers - self._last_total_customers) / elapsed_min
            arrival_rate = total_service_rate + queue_delta_per_min
            return max(arrival_rate, 0.1)

        avg_wait_seconds = sum(q.average_wait_time for q in valid_metrics) / len(valid_metrics)
        backlog_pressure = total_customers / max(avg_wait_seconds / 60.0, 1.0) if total_customers > 0 else 0.0
        return max(total_service_rate, backlog_pressure, 0.1)

    def _determine_urgency(
        self,
        avg_wait_time: float,
        max_wait_time: float,
        total_customers: int,
        current_lanes: int,
    ) -> QueueUrgency:
        customers_per_lane = total_customers / max(current_lanes, 1)

        if avg_wait_time >= self.max_wait_seconds or max_wait_time >= self.max_wait_seconds * 1.2 or customers_per_lane >= 8:
            return QueueUrgency.CRITICAL
        if avg_wait_time >= self.target_wait_seconds * 1.5 or customers_per_lane >= 6:
            return QueueUrgency.URGENT
        if avg_wait_time >= self.target_wait_seconds or customers_per_lane >= 4:
            return QueueUrgency.ATTENTION
        return QueueUrgency.NORMAL

    def _choose_action(
        self,
        current_lanes: int,
        recommended_lanes: int,
        avg_wait_time: float,
        total_customers: int,
    ) -> StaffingAction:
        if recommended_lanes > current_lanes:
            return StaffingAction.OPEN_REGISTER
        if recommended_lanes < current_lanes and total_customers == 0 and avg_wait_time <= 30:
            return StaffingAction.CLOSE_REGISTER
        return StaffingAction.MAINTAIN

    def _calculate_revenue_at_risk(
        self,
        total_customers: int,
        avg_wait_time: float,
        avg_order_value: float,
    ) -> float:
        abandonment_rate = self._interpolate_abandonment_rate(avg_wait_time)
        return round(total_customers * avg_order_value * abandonment_rate, 2)

    def _interpolate_abandonment_rate(self, avg_wait_time: float) -> float:
        wait = max(0.0, avg_wait_time)

        times = sorted(self.abandonment_rate_by_wait.keys())
        rates = [self.abandonment_rate_by_wait[t] for t in times]

        if np is not None:
            return float(np.interp(wait, times, rates))

        if wait <= times[0]:
            return rates[0]
        if wait >= times[-1]:
            return rates[-1]

        for i in range(1, len(times)):
            left_t, right_t = times[i - 1], times[i]
            if left_t <= wait <= right_t:
                left_r, right_r = rates[i - 1], rates[i]
                span = right_t - left_t
                if span <= 0:
                    return left_r
                ratio = (wait - left_t) / span
                return left_r + ratio * (right_r - left_r)

        return rates[-1]

    def _estimate_wait_reduction(
        self,
        current_lanes: int,
        recommended_lanes: int,
        avg_wait_time: float,
    ) -> float:
        if recommended_lanes <= current_lanes:
            return 0.0
        reduction_factor = 1.0 - (current_lanes / recommended_lanes)
        return round(max(0.0, avg_wait_time * reduction_factor), 2)

    def _build_reasoning(
        self,
        total_customers: int,
        avg_wait_time: float,
        max_wait_time: float,
        current_lanes: int,
        recommended_lanes: int,
        action: StaffingAction,
    ) -> str:
        avg_wait_min = round(avg_wait_time / 60.0, 1)
        max_wait_min = round(max_wait_time / 60.0, 1)

        if action == StaffingAction.OPEN_REGISTER:
            additional = recommended_lanes - current_lanes
            return (
                f"Queue bottleneck detected: {total_customers} customers waiting, "
                f"average wait {avg_wait_min} min (longest {max_wait_min} min). "
                f"Open {additional} additional register(s) to reduce wait toward the "
                f"{self.target_wait_seconds // 60}-minute target."
            )

        if action == StaffingAction.CLOSE_REGISTER:
            return (
                f"Queue demand is low: {total_customers} customers waiting and average wait "
                f"{avg_wait_min} min. One register may be closed while maintaining service."
            )

        return (
            f"Queue is stable: {total_customers} customers waiting, average wait "
            f"{avg_wait_min} min (longest {max_wait_min} min). Maintain current staffing."
        )

    def _update_snapshot(self, total_customers: int) -> None:
        self._last_total_customers = total_customers
        self._last_snapshot_at = datetime.now(timezone.utc)


class QueueDataProvider:
    async def get_queue_metrics(self, store_id: str) -> List[QueueMetrics]:
        raise NotImplementedError


class QueueMonitor:
    def __init__(
        self,
        ai_engine: QueueManagementAI,
        data_provider: QueueDataProvider,
        poll_interval_seconds: int = 30,
    ) -> None:
        self.ai_engine = ai_engine
        self.data_provider = data_provider
        self.poll_interval_seconds = max(1, poll_interval_seconds)
        self.running = False
        self._status_by_store: Dict[str, StaffingRecommendation] = {}

    async def monitor_queues(self, store_id: str) -> None:
        self.running = True
        logger.info("Queue monitoring started for store %s", store_id)

        try:
            while self.running:
                try:
                    metrics = await self.data_provider.get_queue_metrics(store_id)
                    recommendation = self.ai_engine.analyze_queue_state(metrics)
                    self._status_by_store[store_id] = recommendation

                    if self.ai_engine.should_send_alert(recommendation):
                        await self._send_alert(store_id, recommendation)

                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Queue monitoring loop failed for store %s", store_id)

                await asyncio.sleep(self.poll_interval_seconds)

        except asyncio.CancelledError:
            logger.info("Queue monitoring task cancelled for store %s", store_id)
            raise
        finally:
            self.running = False
            logger.info("Queue monitoring stopped for store %s", store_id)

    def stop(self) -> None:
        self.running = False

    async def get_current_status(self, store_id: str) -> Optional[StaffingRecommendation]:
        return self._status_by_store.get(store_id)

    async def _send_alert(self, store_id: str, recommendation: StaffingRecommendation) -> None:
        logger.warning(
            "Queue alert for %s: urgency=%s action=%s recommended_lanes=%s revenue_at_risk=$%.2f",
            store_id,
            recommendation.urgency.value,
            recommendation.action.value,
            recommendation.recommended_lanes,
            recommendation.revenue_at_risk,
        )
