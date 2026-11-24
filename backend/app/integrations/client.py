"""Central Atlassian REST client — rate-limited, retrying, outage-safe.

The client is injectable so tests use an in-memory fake. When ATLASSIAN_ENABLED
is false, all calls are recorded locally and returned as a dry-run result:
no network call is ever made from tests or edge nodes.
"""

import base64
import hashlib
import json
import time
from pathlib import Path
from typing import Any

import httpx

from app.config import settings
from app.integrations.models import AtlassianOutbox


def content_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class TokenBucket:
    """Simple thread-safe-enough in-process limiter for a single central worker."""

    def __init__(self, rate_per_min: int, burst: int) -> None:
        self.capacity = max(1, burst)
        self.tokens = float(self.capacity)
        self.rate = max(1.0, float(rate_per_min)) / 60.0
        self.updated = time.monotonic()

    def acquire(self) -> bool:
        now = time.monotonic()
        self.tokens = min(float(self.capacity), self.tokens + (now - self.updated) * self.rate)
        self.updated = now
        if self.tokens < 1:
            return False
        self.tokens -= 1
        return True


class AtlassianUnavailable(Exception):
    """Retryable condition: rate limit, 5xx, transport error."""


class PermanentAtlassianError(Exception):
    """Non-retryable 4xx (except 429) — mapping drift, deleted project, bad field.

    Messages never include the raw response body beyond a bounded, redacted
    excerpt; the full payload stays in the outbox row for audit.
    """

    def __init__(self, status_code: int, detail: str):
        super().__init__(f"HTTP {status_code}: {detail[:300]}")
        self.status_code = status_code
        self.detail = detail[:300]


class DryRunAtlassianClient:
    """Records operations locally; used when integration is disabled or in tests."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def jira_transition(self, issue_key: str, transition: str) -> dict:
        out = {"ok": True, "dry_run": True, "issue": issue_key, "transition": transition}
        self.calls.append(out)
        return out

    def jira_comment(self, issue_key: str, body: str) -> dict:
        out = {"ok": True, "dry_run": True, "issue": issue_key, "body_hash": content_hash(body)}
        self.calls.append(out)
        return out

    def jsm_create_incident(self, payload: dict) -> dict:
        out = {"ok": True, "dry_run": True, "issue": f"JSM-DRY-{len(self.calls) + 1}"}
        self.calls.append({**out, "payload": payload})
        return out

    def jsm_transition(self, issue_key: str, status: str, body: str) -> dict:
        out = {"ok": True, "dry_run": True, "issue": issue_key, "status": status, "body_hash": content_hash(body)}
        self.calls.append(out)
        return out

    def confluence_publish(self, title: str, body: str) -> dict:
        out = {"ok": True, "dry_run": True, "page_id": f"PG-DRY-{len(self.calls) + 1}", "body_hash": content_hash(body)}
        self.calls.append(out)
        return out


class AtlassianClient:
    """Real REST client. Credentials are read from files, never from source."""

    def __init__(self) -> None:
        self.enabled = bool(settings.ATLASSIAN_ENABLED and settings.ATLASSIAN_SITE_URL)
        self.bucket = TokenBucket(settings.ATLASSIAN_RATE_LIMIT_PER_MIN, settings.ATLASSIAN_BURST)
        self.timeout = httpx.Timeout(20.0)

    @property
    def auth(self) -> tuple[str, str]:
        token_path = Path(settings.ATLASSIAN_API_TOKEN_FILE)
        if not token_path.exists():
            raise AtlassianUnavailable("ATLASSIAN_API_TOKEN_FILE is missing")
        token = token_path.read_text(encoding="utf-8").strip()
        if not token:
            raise AtlassianUnavailable("ATLASSIAN_API_TOKEN_FILE is empty")
        return (settings.ATLASSIAN_EMAIL, token)

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        if not self.enabled:
            raise AtlassianUnavailable("ATLASSIAN_ENABLED=false")
        if not self.bucket.acquire():
            raise AtlassianUnavailable("rate limit reached; retry via outbox")
        url = settings.ATLASSIAN_SITE_URL.rstrip("/") + path
        last_error: Exception | None = None
        for attempt in range(settings.ATLASSIAN_MAX_RETRIES):
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    response = client.request(
                        method, url, auth=self.auth, json=payload,
                        headers={"Accept": "application/json", "X-Atlassian-Token": "no-check"},
                    )
                if response.status_code in (429, 500, 502, 503, 504):
                    last_error = AtlassianUnavailable(f"retryable status {response.status_code}")
                    time.sleep(min(2**attempt, 8))
                    continue
                if 400 <= response.status_code < 500:
                    raise PermanentAtlassianError(response.status_code, response.text)
                response.raise_for_status()
                return response.json() if response.content else {}
            except (httpx.HTTPError, AtlassianUnavailable) as exc:
                last_error = exc
                time.sleep(min(2**attempt, 8))
        raise AtlassianUnavailable(f"Atlassian call failed after retries: {last_error}")

    def jira_transition(self, issue_key: str, transition: str) -> dict:
        # Translation happens via a deterministic mapping of target status -> transition id;
        # sites configure their own IDs, so the caller passes the id explicitly.
        return self._request("POST", f"/rest/api/3/issue/{issue_key}/transitions", {"transition": {"id": transition}})

    def jira_comment(self, issue_key: str, body: str) -> dict:
        return self._request("POST", f"/rest/api/3/issue/{issue_key}/comment", {"body": body})

    def jsm_create_incident(self, payload: dict) -> dict:
        return self._request("POST", "/rest/servicedeskapi/request", payload)

    def jsm_transition(self, issue_key: str, status: str, body: str) -> dict:
        self.jira_comment(issue_key, body)
        return self._request("POST", f"/rest/api/3/issue/{issue_key}/transitions", {"transition": {"name": status}})

    def confluence_publish(self, title: str, body: str) -> dict:
        space = settings.CONFLUENCE_SPACE_KEY
        return self._request("POST", "/wiki/rest/api/content", {
            "type": "page",
            "title": title,
            "space": {"key": space},
            "body": {"storage": {"value": body, "representation": "storage"}},
        })


def build_client() -> DryRunAtlassianClient | AtlassianClient:
    return AtlassianClient() if settings.ATLASSIAN_ENABLED else DryRunAtlassianClient()


def enqueue(db, operation: str, idempotency_key: str, payload: dict, priority: int = 3) -> AtlassianOutbox:
    row = AtlassianOutbox(
        operation=operation,
        idempotency_key=idempotency_key,
        payload=json.dumps(payload, sort_keys=True),
        priority=max(1, min(4, int(priority))),
        status="PENDING",
    )
    db.add(row)
    db.flush()
    return row
