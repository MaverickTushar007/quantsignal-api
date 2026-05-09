from app.core.config import BASE_DIR
import json
import requests

def get_all_signals() -> list:
    """Load all signals - try cache file first, fall back to HTTP."""
    # Try cache file first (fastest)
    try:
        cache_path = BASE_DIR / "data/signals_cache.json"
        if cache_path.exists():
            data = json.loads(cache_path.read_text())
            if isinstance(data, dict) and len(data) > 0:
                return list(data.values())
            if isinstance(data, list) and len(data) > 0:
                return data
    except Exception:
        pass

    # Fall back to Redis or internal API call
    try:
        import redis
        from app.core.config import settings
        r = redis.from_url(settings.redis_url)
        keys = r.keys("signal:*")
        if keys:
            signals = []
            for key in keys:
                val = r.get(key)
                if val:
                    signals.append(json.loads(val))
            if signals:
                return signals
    except Exception:
        pass

    # Last resort: call own signals endpoint
    try:
        resp = requests.get("http://localhost:8000/api/v1/signals", timeout=5)
        if resp.ok:
            data = resp.json()
            if isinstance(data, list) and len(data) > 0:
                return data
            if isinstance(data, dict) and len(data) > 0:
                return list(data.values())
    except Exception:
        pass

    return []
