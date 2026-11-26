"""Transport-level defences: CSRF origin checks and in-process rate limiting.

Both are deliberately small and dependency-free. For a multi-replica
production deployment, replace the in-process rate-limit store with Redis;
the interface (``RateLimitMiddleware``) stays the same.
"""

import hashlib
import threading
import time
from collections import defaultdict, deque
from urllib.parse import urlparse

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.config import settings

_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


class CSRFMiddleware(BaseHTTPMiddleware):
    """Reject unsafe requests that carry ambient credentials from a foreign origin.

    Bearer-token-only clients are unaffected because they do not send cookies.
    """

    async def dispatch(self, request: Request, call_next):
        if (
            not settings.CSRF_ENFORCE
            or request.method not in _UNSAFE_METHODS
            or "cookie" not in request.headers
        ):
            return await call_next(request)

        origin = request.headers.get("origin", "")
        allowed = set(settings.ALLOWED_ORIGINS)
        permitted = False
        if "*" in allowed:
            host = request.headers.get("host", "")
            permitted = bool(origin) and urlparse(origin).netloc == host
        elif origin:
            permitted = origin in allowed

        if not permitted:
            return JSONResponse(
                status_code=403,
                content={"detail": "CSRF origin check failed"},
            )
        return await call_next(request)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Sliding-window limiter keyed by authenticated subject (or client IP)."""

    def __init__(self, app, *args, **kwargs):
        super().__init__(app, *args, **kwargs)
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()
        self._requests = 0

    @staticmethod
    def _client_key(request: Request) -> str:
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
            return "tok:" + hashlib.sha256(token.encode()).hexdigest()[:20]
        return "ip:" + (request.client.host if request.client else "unknown")

    @staticmethod
    def _is_login(request: Request) -> bool:
        return request.url.path.rstrip("/") == "/api/v1/auth/login"

    @staticmethod
    def _limit(request: Request) -> int:
        # Login is the most targeted surface, so cap it hardest.
        if RateLimitMiddleware._is_login(request):
            return max(5, min(15, settings.RATE_LIMIT_PER_MINUTE // 8))
        return max(1, settings.RATE_LIMIT_PER_MINUTE)

    async def dispatch(self, request: Request, call_next):
        if not request.url.path.startswith("/api/"):
            return await call_next(request)

        now = time.monotonic()
        # Separate buckets: a low login cap must not count unrelated API traffic.
        prefix = "login:" if self._is_login(request) else "api:"
        key = prefix + self._client_key(request)
        limit = self._limit(request)
        burst = max(1, settings.RATE_LIMIT_BURST)

        with self._lock:
            bucket = self._hits[key]
            while bucket and bucket[0] <= now - 60:
                bucket.popleft()
            recent_burst = sum(1 for t in bucket if t > now - 10)

            if len(bucket) >= limit or recent_burst >= burst:
                oldest = bucket[0] if bucket else now
                retry_after = max(1, int(60 - (now - oldest)))
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Rate limit exceeded"},
                    headers={"Retry-After": str(retry_after)},
                )
            bucket.append(now)

            self._requests += 1
            if self._requests % 1000 == 0:
                stale = [k for k, q in self._hits.items() if not q or q[-1] <= now - 120]
                for k in stale:
                    del self._hits[k]

        return await call_next(request)
