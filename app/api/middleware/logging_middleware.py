"""
api/middleware/logging_middleware.py
Phase 0.4 — Structured logging with correlation IDs.
Every request gets an X-Correlation-ID header for distributed tracing.
"""
import uuid
import time
import logging
from starlette.middleware.base import BaseHTTPMiddleware

log = logging.getLogger("access")


class LoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        correlation_id = str(uuid.uuid4())
        request.state.correlation_id = correlation_id
        start = time.perf_counter()

        response = await call_next(request)

        duration_ms = (time.perf_counter() - start) * 1000
        log.info(
            f"{request.method} {request.url.path} "
            f"→ {response.status_code} "
            f"[{duration_ms:.1f}ms] "
            f"cid={correlation_id}"
        )
        response.headers["X-Correlation-ID"] = correlation_id
        return response
