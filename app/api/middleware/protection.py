"""
app/api/middleware/protection.py
8A: Per-IP rate limiting (Redis-backed)
8B: Adaptive throttling (queue-depth-aware)
"""
import time
import json
import logging
from fastapi import Request
from fastapi.responses import JSONResponse
from app.infrastructure.cache.cache import _get_redis
from app.infrastructure.queue.reasoning_queue import queue_depth

logger = logging.getLogger(__name__)

# 8A — Rate limit config
RATE_LIMIT_REQUESTS = 10
RATE_LIMIT_WINDOW_SECONDS = 10

# 8B — Throttle config
QUEUE_DEPTH_LIMIT = 20


def _get_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    return forwarded.split(",")[0].strip() if forwarded else request.client.host


async def protection_middleware(request: Request, call_next):
    # Skip for health/metrics/docs
    if request.url.path in ["/", "/docs", "/openapi.json", "/api/v1/metrics"]:
        return await call_next(request)

    r = _get_redis()

    # ── 8B: Adaptive throttle (check queue first — fast, no per-IP overhead) ──
    try:
        depth = queue_depth()
        if depth > QUEUE_DEPTH_LIMIT:
            logger.warning(f"[throttle] Queue depth {depth} > {QUEUE_DEPTH_LIMIT} — rejecting")
            return JSONResponse(
                status_code=503,
                content={"detail": "System busy. Try again shortly.", "queue_depth": depth}
            )
    except Exception:
        pass

    # ── 8A: Per-IP rate limiting ──
    if r:
        try:
            ip = _get_ip(request)
            key = f"ratelimit:{ip}"
            now = int(time.time())
            window_start = now - RATE_LIMIT_WINDOW_SECONDS

            # Sliding window using Redis sorted set
            pipe = r.pipeline() if hasattr(r, 'pipeline') else None

            if pipe:
                pipe.zremrangebyscore(key, 0, window_start)
                pipe.zadd(key, {str(now): now})
                pipe.zcard(key)
                pipe.expire(key, RATE_LIMIT_WINDOW_SECONDS * 2)
                results = pipe.execute()
                count = results[2]
            else:
                # Upstash REST doesn't support pipeline — use simple counter
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
                logger.warning(f"[ratelimit] IP {ip} hit limit ({count} reqs)")
                return JSONResponse(
                    status_code=429,
                    content={
                        "detail": f"Rate limit exceeded. Max {RATE_LIMIT_REQUESTS} requests per {RATE_LIMIT_WINDOW_SECONDS}s.",
                        "retry_after": RATE_LIMIT_WINDOW_SECONDS,
                    }
                )
        except Exception as e:
            logger.error(f"[ratelimit] Redis error: {e}")
            # Fail open — don't block if Redis is down

    return await call_next(request)
