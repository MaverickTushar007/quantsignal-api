
from app.core.config import BASE_DIR
import json

def get_all_signals() -> list:
    """Load all signals from cache file."""
    try:
        cache_path = BASE_DIR / "data/signals_cache.json"
        if cache_path.exists():
            data = json.loads(cache_path.read_text())
            if isinstance(data, dict):
                return list(data.values())
            if isinstance(data, list):
                return data
    except Exception:
        pass
    return []
