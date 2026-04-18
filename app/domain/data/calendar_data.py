"""
domain/data/calendar_data.py
Standalone calendar fetch — avoids circular import with api/routes/calendar.py
"""
from app.core.config import BASE_DIR
import json, time
from pathlib import Path

CACHE_PATH = BASE_DIR / "data/calendar_cache.json"
CACHE_TTL  = 3600  # 1 hour for signal generation (shorter than route's 7 days)

def fetch_calendar() -> list:
    """Return cached calendar events. Falls back to route-level cache if available."""
    if CACHE_PATH.exists():
        try:
            cache = json.loads(CACHE_PATH.read_text())
            age = time.time() - cache.get("timestamp", 0)
            if age < CACHE_TTL:
                return cache.get("data", [])
            # Cache stale — try to refresh via route scraper
            try:
                from app.api.routes.calendar import scrape_forexfactory
                events = scrape_forexfactory()
                CACHE_PATH.write_text(json.dumps({
                    "timestamp": time.time(),
                    "data": events
                }))
                return events
            except Exception:
                # Return stale cache rather than nothing
                return cache.get("data", [])
        except Exception:
            pass
    # No cache at all — try scraper
    try:
        from app.api.routes.calendar import scrape_forexfactory
        events = scrape_forexfactory()
        CACHE_PATH.write_text(json.dumps({
            "timestamp": time.time(),
            "data": events
        }))
        return events
    except Exception:
        return []
