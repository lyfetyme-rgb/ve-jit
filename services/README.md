# Edge Sidecars

Two isolated, **stateless** sidecars for the Vendarix edge node. Neither ever
writes to the operational or audit database. The Python edge (FastAPI + its
embedded database) is the single owner of local state, policy evaluation and
audit logging.

| Sidecar | Language | Role |
|---|---|---|
| `hardware_adapter/` | C++ | Unix domain socket daemon accepting newline-delimited JSON commands from SDK-side bridges (NCR Voyix, Toshiba TCGS, Diebold Nixdorf, scanner-scale), forwarding events/heartbeats to the Python edge over its UDS HTTP listener. systemd-sandboxed, 64 MB memory cap, `AF_UNIX`-only. |
| `sync-go/` | Go | Replay classifier for the four-step recovery gate's `watermark_replay` step: pure-commutative SALE batches apply as one aggregate delta; RESTOCK/ADJUSTMENT/TRANSFER vs pending SALEs, oversell vs baseline, unknown kinds, or missing baseline → quarantine for human review. |

Data flow (single owner):

```
native SDKs ──UDS──▶ vendarix-hw-adapter ──UDS──▶ Python edge (owns contracts, policy, audit)
                                                            │
                              outbox snapshot (GET /api/v1/edge/outbox/deltas)
                                                            │
                                                     vejit-sync classifier
                                                            │
                                              verdicts → human review → gated cloud_ack
```

Sidecar invariants (enforced by design — there is no other code path):

- no database handles, no ORM, no policy logic, no credentials of any kind
- hardware_adapter's only outbound socket target is the edge's UDS
- sync-go reads an exported snapshot; it returns verdicts, never mutations
- the edge's `/api/v1/edge/recovery/*` four-step gate remains the only path to ONLINE

Build and test:

```bash
# C++
cd services/hardware_adapter
make                       # g++ -std=c++20 -Wall -Wextra -Werror
./vendarix-hw-adapter --version
# Integration test (Linux): compiles the daemon, round-trips ping/event/heartbeat
python -m pytest test_integration.py -q

# Go
cd services/sync-go
go test ./...
go build ./cmd/vejit-sync
./vejit-sync --baseline baseline.json --input outbox.jsonl --out decisions.jsonl
```

Running the edge over a Unix socket instead of TCP:

```bash
uvicorn app.main:app --uds /run/vendarix/edge.sock    # UDS-only (recommended on the store LAN)
```

Deploy with systemd: see `hardware_adapter/vendarix-hw-adapter.service` and
`infrastructure/kubernetes/` for the container specs (adapt probes to UDS).
