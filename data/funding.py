"""
data/funding.py
Bybit perpetual futures funding rates.
Bybit is not geo-restricted on Railway. Free, no API key.
Cached 1 hour.
"""
import requests
import json
import time
from pathlib import Path

CACHE_PATH = Path("data/funding_cache.json")
CACHE_TTL = 3600

BYBIT_SYMBOL_MAP = {
    "BTC-USD": "BTCUSDT", "ETH-USD": "ETHUSDT", "SOL-USD": "SOLUSDT",
    "BNB-USD": "BNBUSDT", "XRP-USD": "XRPUSDT", "DOGE-USD": "DOGEUSDT",
    "ADA-USD": "ADAUSDT", "AVAX-USD": "AVAXUSDT", "MATIC-USD": "MATICUSDT",
    "DOT-USD": "DOTUSDT", "LINK-USD": "LINKUSDT", "LTC-USD": "LTCUSDT",
    "ATOM-USD": "ATOMUSDT", "NEAR-USD": "NEARUSDT", "OP-USD": "OPUSDT",
    "INJ-USD": "INJUSDT", "FET-USD": "FETUSDT",
}

def get_funding_features(symbol: str) -> dict:
    bybit_symbol = BYBIT_SYMBOL_MAP.get(symbol)
    if not bybit_symbol:
        return {"funding_rate": 0.0, "funding_signal": 0.0,
                "is_overleveraged_long": 0.0, "is_overleveraged_short": 0.0}

    cache = {}
    if CACHE_PATH.exists():
        try:
            cached = json.loads(CACHE_PATH.read_text())
            if time.time() - cached.get("timestamp", 0) < CACHE_TTL:
                if bybit_symbol in cached.get("data", {}):
                    return cached["data"][bybit_symbol]
            cache = cached.get("data", {})
        except Exception:
            pass

    try:
        url = f"https://api.bybit.com/v5/market/funding/history?category=linear&symbol={bybit_symbol}&limit=1"
        resp = requests.get(url, timeout=8).json()
        items = resp.get("result", {}).get("list", [])
        if not items:
            raise ValueError("Empty funding response")

        latest_rate = float(items[0]["fundingRate"])

        is_overleveraged_long = 1.0 if latest_rate > 0.0001 else 0.0
        is_overleveraged_short = 1.0 if latest_rate < -0.0001 else 0.0

        if latest_rate > 0.0003:
            funding_signal = -1.0
        elif latest_rate < -0.0003:
            funding_signal = 1.0
        elif latest_rate > 0.0001:
            funding_signal = -0.5
        elif latest_rate < -0.0001:
            funding_signal = 0.5
        else:
            funding_signal = 0.0

        result = {
            "funding_rate": round(latest_rate * 100, 6),
            "funding_signal": funding_signal,
            "is_overleveraged_long": is_overleveraged_long,
            "is_overleveraged_short": is_overleveraged_short,
        }

        cache[bybit_symbol] = result
        CACHE_PATH.write_text(json.dumps({"timestamp": time.time(), "data": cache}))
        print(f"Bybit funding for {symbol}: {latest_rate*100:.4f}%")
        return result

    except Exception as e:
        print(f"Bybit funding failed for {symbol}: {e}")
        return {"funding_rate": 0.0, "funding_signal": 0.0,
                "is_overleveraged_long": 0.0, "is_overleveraged_short": 0.0}
