from datetime import datetime, timedelta, timezone

from app.core.queue_management_ai import (
    QueueDataProvider,
    QueueManagementAI,
    QueueMetrics,
    QueueMonitor,
    QueueUrgency,
    StaffingAction,
)


def make_metric(
    lane_id: str = "lane_1",
    customers_in_line: int = 5,
    average_wait_time: float = 300.0,
    items_per_cart: float = 20.0,
    cashier_speed: float = 18.0,
    estimated_wait_new_customer: float = 360.0,
):
    return QueueMetrics(
        lane_id=lane_id,
        customers_in_line=customers_in_line,
        average_wait_time=average_wait_time,
        items_per_cart=items_per_cart,
        cashier_speed=cashier_speed,
        estimated_wait_new_customer=estimated_wait_new_customer,
        timestamp=datetime.now(timezone.utc),
    )


def test_empty_metrics_handling():
    ai = QueueManagementAI()
    recommendation = ai.analyze_queue_state([])
    assert recommendation.action == StaffingAction.MAINTAIN
    assert recommendation.recommended_lanes == 1
    assert "No queue data" in recommendation.reasoning


def test_invalid_metrics_handling():
    ai = QueueManagementAI()
    bad = make_metric(customers_in_line=-1)
    recommendation = ai.analyze_queue_state([bad])
    assert recommendation.action == StaffingAction.MAINTAIN
    assert "Invalid queue data" in recommendation.reasoning


def test_division_by_zero_protection():
    ai = QueueManagementAI()
    metrics = [make_metric(items_per_cart=0.0, cashier_speed=0.0)]
    recommendation = ai.analyze_queue_state(metrics)
    assert recommendation is not None


def test_revenue_interpolation():
    ai = QueueManagementAI(avg_order_value=45.0)
    revenue = ai._calculate_revenue_at_risk(total_customers=10, avg_wait_time=210.0, avg_order_value=45.0)
    assert 22.5 < revenue < 67.5


def test_alert_cooldown_uses_total_seconds():
    ai = QueueManagementAI(alert_cooldown_seconds=300)
    rec = ai.analyze_queue_state([make_metric(average_wait_time=400.0)])
    assert rec.urgency in {QueueUrgency.ATTENTION, QueueUrgency.URGENT, QueueUrgency.CRITICAL}

    now = datetime.now(timezone.utc)
    assert ai.should_send_alert(rec, now=now) is True
    assert ai.should_send_alert(rec, now=now + timedelta(seconds=60)) is False
    assert ai.should_send_alert(rec, now=now + timedelta(seconds=301)) is True


class DummyProvider(QueueDataProvider):
    async def get_queue_metrics(self, store_id: str):
        return [make_metric()]


def test_monitor_stop_flag():
    ai = QueueManagementAI()
    monitor = QueueMonitor(ai, DummyProvider(), poll_interval_seconds=1)
    assert monitor.running is False
    monitor.stop()
    assert monitor.running is False
