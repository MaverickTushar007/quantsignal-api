"""
infrastructure/queue/reasoning_queue.py
Redis-backed job queue for async reasoning.
Uses Upstash lpush/rpop — no blocking calls, works over REST.
"""
import json
import logging
from datetime import datetime, timedelta
from app.infrastructure.cache.cache import _get_redis

logger = logging.getLogger(__name__)

QUEUE_KEY = "reasoning_jobs"
STATUS_KEY_PREFIX = "reasoning_status:"
REASONING_TTL_MINUTES = 15


def _status_key(symbol: str) -> str:
    return f"{STATUS_KEY_PREFIX}{symbol}"


def enqueue_reasoning_job(symbol: str, signal: dict) -> bool:
    """Push a reasoning job onto the queue. Returns True if enqueued."""
    try:
        r = _get_redis()
        if not r:
            logger.warning("[queue] Redis unavailable — job not enqueued")
            return False

        # ── Deduplication guard ──────────────────────────────────────
        key = _status_key(symbol)
        raw = r.get(key)
        if raw:
            state = json.loads(raw)
            status = state.get("status")

            if status == "pending":
                logger.info(f"[queue] Skipping {symbol} — already pending")
                return False

            if status == "complete":
                updated_at = state.get("updated_at")
                if updated_at:
                    age = datetime.now() - datetime.fromisoformat(updated_at)
                    if age < timedelta(minutes=REASONING_TTL_MINUTES):
                        logger.info(f"[queue] Skipping {symbol} — complete and fresh ({int(age.total_seconds())}s old)")
                        return False

            # status == "failed" → fall through and allow re-enqueue
        # ────────────────────────────────────────────────────────────

        # Mark pending BEFORE pushing to queue
        r.set(key, json.dumps({"status": "pending", "updated_at": datetime.now().isoformat()}))

        job = json.dumps({"symbol": symbol, "signal": signal})
        r.lpush(QUEUE_KEY, job)
        logger.info(f"[queue] Enqueued reasoning job for {symbol}")
        return True

    except Exception as e:
        logger.error(f"[queue] Enqueue failed for {symbol}: {e}")
        return False


def mark_reasoning_complete(symbol: str) -> None:
    """Call this from the worker when reasoning finishes successfully."""
    try:
        r = _get_redis()
        if r:
            key = _status_key(symbol)
            r.set(key, json.dumps({"status": "complete", "updated_at": datetime.now().isoformat()}))
            logger.info(f"[queue] Marked {symbol} reasoning complete")
    except Exception as e:
        logger.error(f"[queue] mark_complete failed for {symbol}: {e}")


def mark_reasoning_failed(symbol: str) -> None:
    """Call this from the worker on failure — allows re-enqueue."""
    try:
        r = _get_redis()
        if r:
            key = _status_key(symbol)
            r.set(key, json.dumps({"status": "failed", "updated_at": datetime.now().isoformat()}))
            logger.info(f"[queue] Marked {symbol} reasoning failed")
    except Exception as e:
        logger.error(f"[queue] mark_failed failed for {symbol}: {e}")


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
