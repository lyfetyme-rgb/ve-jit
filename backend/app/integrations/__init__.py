"""Central-only Atlassian automation: release/audit, store self-healing, outage-safe delivery.

HARD RULE: no module under app/edge/ or the edge sidecars imports this package.
Edge nodes never hold Atlassian credentials or call Atlassian Cloud APIs directly.
All telemetry is batched, deduplicated and rate-limited here (central ve-jit backend).
"""
