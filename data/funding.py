"""
data/funding.py
Fetches crypto perpetual futures funding rates from Binance.
Completely free, no API key required.
Used as additional signal features for crypto assets.
"""
import requests
import time
import json
from pathlib import Path

CACHE_PATH = Path("data/funding_cache.json")
CACHE_TTL = 3600  # 1 hour

SYMBOL_MAP = {
    "BTC-USD": "BTCUSDT",
    "ETH-USD": "ETHUSDT",
    "SOL-USD": "SOLUSDT",
    "BNB-USD": "BNBUSDT",
    "XRP-USD": "XRPUSDT",
    "DOGE-USD": "DOGEUSDT",
    "ADA-USD": "ADAUSDT",
    "AVAX-USD": "AVAXUSDT",
    "MATIC-USD": "MATICUSDT",
    "DOT-USD": "DOTUSDT",
    "LINK-USD": "LINKUSDT",
    "LTC-USD": "LTCUSDT",
    "ATOM-USD": "ATOMUSDT",
    "NEAR-USD": "NEARUSDT",
    "OP-USD": "OPUSDT",
    "INJ-USD": "INJUSDT",
    "FET-USD": "FETUSDT",
}

def _load_cache():
    if CACHE_PATH.exists():
        try:
            cache = json.loads(CACHE_PATH.read_text())
            if time.time() - cache.get("timestamp", 0) < CACHE_TTL:
                return cache.get("data", {})
        except Exception:
            pass
    return {}

def _save_cache(data: dict):
    try:
        CACHE_PATH.write_text(json.dumps({
            "timestamp": time.time(),
            "data": data
        }))
    except Exception:
        pass

def get_funding_features(symbol: str) -> dict:
    """
    Returns funding rate features for a crypto symbol.
    Returns neutral values for non-crypto assets.
    """
    binance_symbol = SYMBOL_MAP.get(symbol)
    if not binance_symbol:
        return {
            "funding_rate": 0.0,
            "funding_signal": 0.0,
            "is_overleveraged_long": 0.0,
            "is_overleveraged_short": 0.0,
        }

    # Check cache
    cache = _load_cache()
    if binance_symbol in cache:
        return cache[binance_symbol]

    try:
        # Get latest funding rate
        url = f"https://fapi.binance.com/fapi/v1/fundingRate?symbol={binance_symbol}&limit=3"
        resp = requests.get(url, timeout=5)
        data = resp.json()

        if not data or not isinstance(data, list):
            raise ValueError("Empty response")

        latest_rate = float(data[-1]["fundingRate"])
        
        # Funding rate interpretation:
        # > +0.01% (0.0001) = overleveraged longs = bearish signal
        # < -0.01% (-0.0001) = overleveraged shorts = bullish signal
        # Between = neutral
        
        is_overleveraged_long = 1.0 if latest_rate > 0.0001 else 0.0
        is_overleveraged_short = 1.0 if latest_rate < -0.0001 else 0.0
        
        # Funding signal: -1 (very bearish) to +1 (very bullish)
        # Positive funding = bearish (longs paying shorts = crowded long)
        # Negative funding = bullish (shorts paying longs = crowded short)
        funding_signal = -1.0 if latest_rate > 0.0003 else \
                          1.0 if latest_rate < -0.0003 else \
                         -0.5 if latest_rate > 0.0001 else \
                          0.5 if latest_rate < -0.0001 else 0.0

        result = {
            "funding_rate": round(latest_rate * 100, 6),  # as percentage
            "funding_signal": funding_signal,
            "is_overleveraged_long": is_overleveraged_long,
            "is_overleveraged_short": is_overleveraged_short,
        }

        # Cache it
        cache[binance_symbol] = result
        _save_cache(cache)

        return result

    except Exception as e:
        print(f"Funding rate fetch failed for {symbol}: {e}")
        return {
            "funding_rate": 0.0,
            "funding_signal": 0.0,
            "is_overleveraged_long": 0.0,
            "is_overleveraged_short": 0.0,
        }
