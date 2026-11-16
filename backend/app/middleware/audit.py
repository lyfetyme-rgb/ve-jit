import json
from datetime import datetime, timezone
from pathlib import Path

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request


class AuditLogMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, log_path: str = "audit_logs/audit.log"):
        super().__init__(app)
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    async def dispatch(self, request: Request, call_next):
        started = datetime.now(timezone.utc)
        response = await call_next(request)
        ended = datetime.now(timezone.utc)

        record = {
            "timestamp": started.isoformat(),
            "method": request.method,
            "path": request.url.path,
            "query": str(request.url.query),
            "client": request.client.host if request.client else None,
            "status_code": response.status_code,
            "duration_ms": round((ended - started).total_seconds() * 1000, 2),
            "user_agent": request.headers.get("user-agent"),
        }

        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")

        return response
