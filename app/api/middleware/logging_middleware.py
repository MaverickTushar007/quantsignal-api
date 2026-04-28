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

        try:
            response = await call_next(request)
        except Exception as _exc:
            import traceback
            log.error(f"UNHANDLED EXCEPTION {request.url.path}: {traceback.format_exc()}")
            raise

        duration_ms = (time.perf_counter() - start) * 1000
        log.info(
            f"{request.method} {request.url.path} "
            f"→ {response.status_code} "
            f"[{duration_ms:.1f}ms] "
            f"cid={correlation_id}"
        )
        response.headers["X-Correlation-ID"] = correlation_id
        response.headers["X-Response-Time-Ms"] = f"{duration_ms:.1f}"

        # Alert on slow routes
        THRESHOLDS = {
            "/research/": 8000,
            "/documents/": 15000,
            "/portfolio/": 5000,
        }
        for path_prefix, threshold in THRESHOLDS.items():
            if path_prefix in request.url.path and duration_ms > threshold:
                log.warning(
                    f"SLOW_ROUTE {request.url.path} took {duration_ms:.0f}ms "
                    f"(threshold {threshold}ms) cid={correlation_id}"
                )
        return response
