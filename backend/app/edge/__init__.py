"""Vendarix edge runtime (in-process).

The edge node is the operational engine: it receives hardware and enterprise
events, normalizes them into shared contracts, validates them, runs approved
local AI skills, applies deterministic business policies, requests human
authorization where required, persists authorized operational changes, queues
events for cloud synchronization, and recovers/reconciles after connectivity
returns.

Runtime layout in this Python application:

- app/edge/contracts.py   — shared event contract (Vendarix manifest)
- app/edge/adapters/      — NCR Voyix, Toshiba TCGS (JavaPOS/UPOS),
                            Diebold Nixdorf, scanner-scale, enterprise ERP/WMS/supplier
- app/edge/policy.py      — deterministic policy evaluation (no model in the loop)
- app/edge/continuity.py  — continuity state machine + continuity value
- app/edge/fabric.py      — ingest -> validate -> normalize -> identity -> route ->
                            policy -> authorization -> outbox pipeline
- app/edge/skills.py      — local AI skill runner (subprocess, JSON stdin/stdout)
- skills/                 — skill packages (run.ts + skill.py + schemas + tests)
- app/api/edge.py         — HTTP surface
"""
