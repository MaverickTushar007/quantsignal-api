"""
core/cache.py
Redis caching layer using Upstash REST API.
TTL: 1 hour for signals, 5 minutes for market mood.
"""
import json
import os
from dotenv import load_dotenv

load_dotenv()

_redis = None

def _get_redis():
    global _redis
    if _redis is None:
        try:
            from upstash_redis import Redis
            _redis = Redis(
                url=os.getenv("UPSTASH_REDIS_REST_URL"),
                token=os.getenv("UPSTASH_REDIS_REST_TOKEN")
            )
        except Exception as e:
            print(f"Redis init failed: {e}")
            return None
    return _redis

def get_cached(key: str):
    try:
        r = _get_redis()
        if not r:
            return None
        val = r.get(key)
        if val:
            return json.loads(val)
    except Exception as e:
        print(f"Cache get failed: {e}")
    return None

def set_cached(key: str, value: dict, ttl: int = 3600):
    try:
        r = _get_redis()
        if not r:
            return
        r.setex(key, ttl, json.dumps(value))
    except Exception as e:
        print(f"Cache set failed: {e}")

def invalidate(key: str):
    try:
        r = _get_redis()
        if r:
            r.delete(key)
    except Exception as e:
        print(f"Cache invalidate failed: {e}")

# ── Bulk signal cache (survives Render restarts) ─────────────────────────────
BULK_KEY = "signals_cache_bulk"

def save_bulk_cache(cache: dict, ttl: int = 86400):
    """Persist entire signals cache to Redis. TTL=24h."""
    try:
        r = _get_redis()
        if not r:
            return
        r.setex(BULK_KEY, ttl, json.dumps(cache))
        print(f"[cache] Bulk cache saved to Redis: {len(cache)} signals")
    except Exception as e:
        print(f"[cache] Bulk save failed: {e}")

def load_bulk_cache() -> dict:
    """Load bulk cache from Redis. Returns {} if missing."""
    try:
        r = _get_redis()
        if not r:
            return {}
        val = r.get(BULK_KEY)
        if val:
            data = json.loads(val)
            print(f"[cache] Bulk cache loaded from Redis: {len(data)} signals")
            return data
    except Exception as e:
        print(f"[cache] Bulk load failed: {e}")
    return {}
