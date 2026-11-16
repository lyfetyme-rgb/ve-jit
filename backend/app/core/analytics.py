from typing import Dict, List


class AnalyticsEngine:
    def summarize_sales(self, values: List[float]) -> Dict[str, float]:
        if not values:
            return {"count": 0, "total": 0.0, "average": 0.0}
        total = float(sum(values))
        return {"count": len(values), "total": total, "average": total / len(values)}
