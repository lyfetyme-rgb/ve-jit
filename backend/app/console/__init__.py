"""Store Console — a working multi-tenant retail application surface.

Three demo tenants ship with the platform:

- Food Lion (grocery / cold chain)
- Ace Hardware (hardware / paint / tools)
- Lowe's (home improvement / appliances / lumber)

Each tenant has stores, SKUs, prices, inventory, activity and governed
recommendations. Tenant/store scoping is enforced on every route; the console
never handles cardholder data and recommendations still require approval before
Operations executes them.
"""

from app.console.models import ConsoleStore, ConsoleTenant

__all__ = ["ConsoleTenant", "ConsoleStore"]
