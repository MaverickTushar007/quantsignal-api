"""
data/fear_greed.py
CNN Fear & Greed Index via alternative.me API.
Free, no API key required.
Cached for 1 hour.
"""
import requests
import json
import time
from pathlib import Path

CACHE_PATH = Path("data/fear_greed_cache.json")
CACHE_TTL = 3600  # 1 hour

def get_fear_greed() -> dict:
    """
    Returns Fear & Greed Index data.
    Score: 0 = Extreme Fear, 100 = Extreme Greed
    Contrarian signal: Extreme Fear = potential BUY, Extreme Greed = potential SELL
    """
    # Check cache
    if CACHE_PATH.exists():
        try:
            cache = json.loads(CACHE_PATH.read_text())
            if time.time() - cache.get("timestamp", 0) < CACHE_TTL:
                return cache.get("data")
        except Exception:
            pass

    try:
        resp = requests.get("https://api.alternative.me/fng/?limit=2", timeout=5)
        raw = resp.json()
        latest = raw["data"][0]
        prev = raw["data"][1] if len(raw["data"]) > 1 else latest

        score = int(latest["value"])
        prev_score = int(prev["value"])
        classification = latest["value_classification"]

        # Contrarian signals
        extreme_fear = 1.0 if score <= 25 else 0.0
        extreme_greed = 1.0 if score >= 75 else 0.0
        fear_improving = 1.0 if score > prev_score and score <= 40 else 0.0

        # Signal: +1 = bullish contrarian, -1 = bearish contrarian
        if score <= 25:
            contrarian_signal = 1.0   # extreme fear = contrarian buy
        elif score >= 75:
            contrarian_signal = -1.0  # extreme greed = contrarian sell
        elif score <= 40:
            contrarian_signal = 0.5   # fear = mild bullish
        elif score >= 60:
            contrarian_signal = -0.5  # greed = mild bearish
        else:
            contrarian_signal = 0.0   # neutral

        result = {
            "score": score,
            "classification": classification,
            "prev_score": prev_score,
            "extreme_fear": extreme_fear,
            "extreme_greed": extreme_greed,
            "fear_improving": fear_improving,
            "contrarian_signal": contrarian_signal,
        }

        CACHE_PATH.write_text(json.dumps({
            "timestamp": time.time(),
            "data": result
        }))

        return result

    except Exception as e:
        print(f"Fear & Greed fetch failed: {e}")
        return {
            "score": 50,
            "classification": "Neutral",
            "prev_score": 50,
            "extreme_fear": 0.0,
            "extreme_greed": 0.0,
            "fear_improving": 0.0,
            "contrarian_signal": 0.0,
        }
