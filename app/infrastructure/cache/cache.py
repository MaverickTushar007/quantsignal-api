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
            url   = os.getenv("UPSTASH_REDIS_REST_URL")
            token = os.getenv("UPSTASH_REDIS_REST_TOKEN")
            if not url or not token:
                return None
            from upstash_redis import Redis
            _redis = Redis(url=url, token=token)
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
