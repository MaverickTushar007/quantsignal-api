"""
app/api/routes/metrics.py
Internal observability endpoint — queue depth, job stats, reasoning latency.
"""
import json
import logging
from datetime import datetime, timedelta
from fastapi import APIRouter
from app.infrastructure.queue.reasoning_queue import (
    queue_depth,
    STATUS_KEY_PREFIX,
    REASONING_TTL_MINUTES,
)
from app.infrastructure.cache.cache import _get_redis

router = APIRouter()
logger = logging.getLogger(__name__)

TRACKED_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
    "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT",
]

def _get_all_reasoning_states() -> list[dict]:
    """Fetch reasoning states for known symbols — avoids KEYS scan."""
    states = []
    try:
        r = _get_redis()
        if not r:
            return states
        for symbol in TRACKED_SYMBOLS:
            key = f"{STATUS_KEY_PREFIX}{symbol}"
            raw = r.get(key)
            if raw:
                state = json.loads(raw)
                state["symbol"] = symbol
                states.append(state)
    except Exception as e:
        logger.error(f"[metrics] Redis fetch failed: {e}")
    return states


@router.get("/metrics", tags=["observability"])
def get_metrics():
    states = _get_all_reasoning_states()
    now = datetime.now()

    counts = {"pending": 0, "complete": 0, "failed": 0}
    stale = []
    recent_completions = []

    for s in states:
        status = s.get("status", "unknown")
        counts[status] = counts.get(status, 0) + 1

        updated_at = s.get("updated_at")
        if updated_at:
            age = now - datetime.fromisoformat(updated_at)
            age_minutes = age.total_seconds() / 60

            if status == "complete":
                recent_completions.append({
                    "symbol": s["symbol"],
                    "age_minutes": round(age_minutes, 1),
                })
                if age_minutes > REASONING_TTL_MINUTES:
                    stale.append(s["symbol"])

            if status == "pending" and age_minutes > 2:
                stale.append(f"{s['symbol']} (stuck pending)")

    return {
        "timestamp": now.isoformat(),
        "queue": {
            "depth": queue_depth(),
            "pending_jobs": counts["pending"],
        },
        "reasoning": {
            "complete": counts["complete"],
            "failed": counts["failed"],
            "pending": counts["pending"],
            "stale": stale,
            "recent_completions": sorted(
                recent_completions, key=lambda x: x["age_minutes"]
            )[:10],
        },
        "health": {
            "status": "degraded" if counts["failed"] > 2 or len(stale) > 3 else "ok",
            "failed_jobs": counts["failed"],
            "stale_count": len(stale),
        },
    }
