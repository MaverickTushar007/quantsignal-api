"""
infrastructure/queue/reasoning_queue.py
Redis-backed job queue for async reasoning.
Uses Upstash lpush/rpop — no blocking calls, works over REST.
"""

import json
import logging
from app.infrastructure.cache.cache import _get_redis

logger = logging.getLogger(__name__)

QUEUE_KEY = "reasoning_jobs"


def enqueue_reasoning_job(symbol: str, signal: dict) -> bool:
    """Push a reasoning job onto the queue. Returns True if enqueued."""
    try:
        r = _get_redis()
        if not r:
            logger.warning("[queue] Redis unavailable — job not enqueued")
            return False
        job = json.dumps({"symbol": symbol, "signal": signal})
        r.lpush(QUEUE_KEY, job)
        logger.info(f"[queue] Enqueued reasoning job for {symbol}")
        return True
    except Exception as e:
        logger.error(f"[queue] Enqueue failed for {symbol}: {e}")
        return False


def dequeue_reasoning_job() -> dict | None:
    """Pop one job from the queue. Returns None if empty."""
    try:
        r = _get_redis()
        if not r:
            return None
        val = r.rpop(QUEUE_KEY)
        if val:
            return json.loads(val)
    except Exception as e:
        logger.error(f"[queue] Dequeue failed: {e}")
    return None


def queue_depth() -> int:
    """How many jobs are waiting."""
    try:
        r = _get_redis()
        if not r:
            return 0
        return r.llen(QUEUE_KEY) or 0
    except Exception:
        return 0
