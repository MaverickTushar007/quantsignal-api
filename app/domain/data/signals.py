from app.core.config import BASE_DIR, settings
import json
import os

def get_all_signals() -> list:
    """Load all signals from cache file (primary) or env-configured API URL (fallback)."""
    # Primary: cache file
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

    # Fallback: call signals endpoint using own public URL
    try:
        import requests
        base_url = os.getenv("RENDER_EXTERNAL_URL", "")
        if not base_url:
            # Try common Render env vars
            service = os.getenv("RENDER_SERVICE_NAME", "")
            if service:
                base_url = f"https://{service}.onrender.com"
        if not base_url:
            base_url = "https://quantsignal-api.onrender.com"
        resp = requests.get(f"{base_url}/api/v1/signals", timeout=8)
        if resp.ok:
            data = resp.json()
            if isinstance(data, list) and len(data) > 0:
                return data
            if isinstance(data, dict) and len(data) > 0:
                return list(data.values())
    except Exception:
        pass

    return []
