#!/usr/bin/env python3
"""Small, dependency-light load test for a running VE-JIT API.

Credentials come from the environment, never argv:

    VEJIT_TEST_TOKEN=<bearer>              # or
    VEJIT_TEST_USER / VEJIT_TEST_PASSWORD  # logs in via /api/v1/auth/login

Example:
    python tools/load_test.py --url http://127.0.0.1:8000 --duration 30 --concurrency 10

Reports p50/p95/p99 latency, requests/sec and non-2xx counts. This is a
smoke/load harness, not a substitute for a full performance assessment.
"""

import argparse
import asyncio
import os
import statistics
import time

import httpx


async def get_token(client: httpx.AsyncClient, base: str) -> str:
    token = os.environ.get("VEJIT_TEST_TOKEN", "").strip()
    if token:
        return token
    user = os.environ.get("VEJIT_TEST_USER", "").strip()
    password = os.environ.get("VEJIT_TEST_PASSWORD", "").strip()
    if not (user and password):
        raise SystemExit("Set VEJIT_TEST_TOKEN or VEJIT_TEST_USER/VEJIT_TEST_PASSWORD")
    response = await client.post(
        f"{base}/api/v1/auth/login", json={"username": user, "password": password}
    )
    response.raise_for_status()
    return response.json()["access_token"]


async def worker(client: httpx.AsyncClient, base: str, headers: dict, paths: list, stop_at: float, results: list):
    i = 0
    while time.monotonic() < stop_at:
        path = paths[i % len(paths)]
        i += 1
        started = time.perf_counter()
        try:
            response = await client.get(base + path, headers=headers)
            status = response.status_code
        except Exception:
            status = 0
        results.append(((time.perf_counter() - started) * 1000, status))


def percentile(values: list, pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(len(ordered) * pct))
    return ordered[index]


async def run(args) -> None:
    base = args.url.rstrip("/")
    async with httpx.AsyncClient(timeout=args.timeout) as client:
        token = await get_token(client, base)
        headers = {"Authorization": f"Bearer {token}"}

        catalog = (await client.get(f"{base}/api/v1/console/catalog", headers=headers)).json()
        tenant = catalog["tenants"][0]["tenant_id"]
        store = catalog["stores"][0]["store_id"]
        paths = [
            "/health/ready",
            "/api/v1/console/catalog",
            f"/api/v1/console/overview?tenant_id={tenant}&store_id={store}",
            f"/api/v1/console/inventory?tenant_id={tenant}&store_id={store}",
        ]

        results: list = []
        stop_at = time.monotonic() + args.duration
        tasks = [
            asyncio.create_task(worker(client, base, headers, paths, stop_at, results))
            for _ in range(args.concurrency)
        ]
        await asyncio.gather(*tasks)

    latencies = [r[0] for r in results]
    errors = [r for r in results if r[1] < 200 or r[1] >= 300]
    elapsed = max(args.duration, 0.001)
    print(f"requests: {len(results)}  concurrency: {args.concurrency}  duration: {args.duration}s")
    print(f"rps: {len(results) / elapsed:.1f}  errors: {len(errors)}")
    print(
        f"latency ms  p50={percentile(latencies, .50):.1f}  "
        f"p95={percentile(latencies, .95):.1f}  p99={percentile(latencies, .99):.1f}  "
        f"mean={statistics.fmean(latencies) if latencies else 0:.1f}"
    )
    if errors:
        codes = {}
        for _, status in errors:
            codes[status] = codes.get(status, 0) + 1
        print("error status counts:", codes)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--duration", type=float, default=30.0)
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--timeout", type=float, default=10.0)
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
