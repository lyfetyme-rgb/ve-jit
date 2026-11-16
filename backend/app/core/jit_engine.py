import math
from dataclasses import asdict, dataclass


@dataclass
class ReorderDecision:
    sku: str
    current_stock: int
    reorder_point: float
    economic_order_quantity: float
    should_reorder: bool


class JITEngine:
    def calculate_eoq(self, annual_demand: float, unit_cost: float, holding_cost_rate: float) -> float:
        if annual_demand <= 0 or unit_cost <= 0 or holding_cost_rate <= 0:
            return 0.0
        ordering_cost = 50.0
        holding_cost = unit_cost * holding_cost_rate
        return round(math.sqrt((2 * annual_demand * ordering_cost) / holding_cost), 2)

    def calculate_reorder_point(self, demand_rate: float, lead_time_days: float, safety_stock: float) -> float:
        return round((demand_rate * lead_time_days) + safety_stock, 2)

    def evaluate(self, sku: str, current_stock: int, annual_demand: float, unit_cost: float,
                 holding_cost_rate: float, demand_rate: float, lead_time_days: float,
                 safety_stock: float) -> dict:
        eoq = self.calculate_eoq(annual_demand, unit_cost, holding_cost_rate)
        reorder_point = self.calculate_reorder_point(demand_rate, lead_time_days, safety_stock)
        decision = ReorderDecision(
            sku=sku,
            current_stock=current_stock,
            reorder_point=reorder_point,
            economic_order_quantity=eoq,
            should_reorder=current_stock <= reorder_point,
        )
        return asdict(decision)
