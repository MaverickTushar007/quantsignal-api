import time
import json
import logging
from fastapi import Request
from fastapi.responses import JSONResponse
from app.infrastructure.cache.cache import _get_redis
from app.infrastructure.queue.reasoning_queue import queue_depth

logger = logging.getLogger(__name__)

RATE_LIMIT_REQUESTS = 10
RATE_LIMIT_WINDOW_SECONDS = 10
QUEUE_DEPTH_LIMIT = 20

def _get_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    return forwarded.split(",")[0].strip() if forwarded else request.client.host

async def protection_middleware(request: Request, call_next):
    if request.url.path in ["/", "/docs", "/openapi.json", "/api/v1/metrics", "/api/v1/health"]:
        return await call_next(request)

    r = _get_redis()

    # 8B: Queue-based throttle
    try:
        depth = queue_depth()
        if depth > QUEUE_DEPTH_LIMIT:
            return JSONResponse(status_code=503,
                content={"detail": "System busy. Try again shortly.", "queue_depth": depth})
    except Exception:
        pass

    # 8A: Per-IP rate limit
    if r:
        try:
            ip = _get_ip(request)
            key = f"ratelimit:{ip}"
            now = int(time.time())
            raw = r.get(key)
            if raw:
                data = json.loads(raw)
                if now - data["window_start"] < RATE_LIMIT_WINDOW_SECONDS:
                    count = data["count"] + 1
                else:
                    count = 1
                    data = {"window_start": now, "count": 1}
            else:
                count = 1
                data = {"window_start": now, "count": 1}
            data["count"] = count
            r.set(key, json.dumps(data))
            if count > RATE_LIMIT_REQUESTS:
                return JSONResponse(status_code=429,
                    content={"detail": f"Rate limit exceeded. Max {RATE_LIMIT_REQUESTS} per {RATE_LIMIT_WINDOW_SECONDS}s.", "retry_after": RATE_LIMIT_WINDOW_SECONDS})
        except Exception as e:
            logger.error(f"[ratelimit] {e}")

    return await call_next(request)
