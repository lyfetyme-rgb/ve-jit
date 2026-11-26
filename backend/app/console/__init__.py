"""Store Console — a working multi-tenant retail application surface.

Retailers are loaded from a JSON catalog (``config/retailers.demo.json`` by
default), so no real company names or SKU namespaces are baked into code. The
shipped catalog contains generic grocery, hardware and home-improvement
archetypes; copy and edit it to load your own companies.

Every tenant has stores, prices, inventory, activity and governed
recommendations. Tenant/store scoping is enforced on every route, the console
never handles cardholder data, and recommendations still require approval
before Operations executes them.
"""

from app.console.models import ConsoleStore, ConsoleTenant

__all__ = ["ConsoleTenant", "ConsoleStore"]
