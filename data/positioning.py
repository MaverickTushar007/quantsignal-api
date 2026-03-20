"""
data/positioning.py
Binance futures positioning data — Long/Short ratio + Open Interest.
Free, no API key required. Cached 15 minutes.
"""
import requests
import json
import time
from pathlib import Path

CACHE_PATH = Path("data/positioning_cache.json")
CACHE_TTL = 900  # 15 minutes

SYMBOL_MAP = {
    "BTC-USD": "BTCUSDT", "ETH-USD": "ETHUSDT", "SOL-USD": "SOLUSDT",
    "BNB-USD": "BNBUSDT", "XRP-USD": "XRPUSDT", "DOGE-USD": "DOGEUSDT",
    "ADA-USD": "ADAUSDT", "AVAX-USD": "AVAXUSDT", "MATIC-USD": "MATICUSDT",
    "DOT-USD": "DOTUSDT", "LINK-USD": "LINKUSDT", "LTC-USD": "LTCUSDT",
    "ATOM-USD": "ATOMUSDT", "NEAR-USD": "NEARUSDT", "OP-USD": "OPUSDT",
    "INJ-USD": "INJUSDT", "FET-USD": "FETUSDT",
}

def get_positioning(symbol: str) -> dict:
    binance_symbol = SYMBOL_MAP.get(symbol)
    if not binance_symbol:
        return {
            "long_ratio": 0.5, "short_ratio": 0.5,
            "long_short_ratio": 1.0, "open_interest": 0.0,
            "crowded_long": 0.0, "crowded_short": 0.0,
            "positioning_signal": 0.0,
        }

    # Check cache
    cache = {}
    if CACHE_PATH.exists():
        try:
            cached = json.loads(CACHE_PATH.read_text())
            if time.time() - cached.get("timestamp", 0) < CACHE_TTL:
                if binance_symbol in cached.get("data", {}):
                    return cached["data"][binance_symbol]
            cache = cached.get("data", {})
        except Exception:
            pass

    try:
        # Long/Short ratio
        ls_url = f"https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol={binance_symbol}&period=5m&limit=1"
        ls_resp = requests.get(ls_url, timeout=5).json()
        long_ratio = float(ls_resp[0]["longAccount"])
        short_ratio = float(ls_resp[0]["shortAccount"])
        ls_ratio = float(ls_resp[0]["longShortRatio"])

        # Open interest
        oi_url = f"https://fapi.binance.com/fapi/v1/openInterest?symbol={binance_symbol}"
        oi_resp = requests.get(oi_url, timeout=5).json()
        open_interest = float(oi_resp["openInterest"])

        # Signals
        # Crowded long = contrarian bearish (>65% longs)
        crowded_long = 1.0 if long_ratio > 0.65 else 0.0
        # Crowded short = contrarian bullish (<35% longs)
        crowded_short = 1.0 if long_ratio < 0.35 else 0.0

        # Positioning signal: +1 = bullish contrarian, -1 = bearish contrarian
        if long_ratio > 0.65:
            positioning_signal = -1.0  # too many longs = bearish contrarian
        elif long_ratio < 0.35:
            positioning_signal = 1.0   # too many shorts = bullish contrarian
        elif long_ratio > 0.58:
            positioning_signal = -0.5  # mildly crowded long
        elif long_ratio < 0.42:
            positioning_signal = 0.5   # mildly crowded short
        else:
            positioning_signal = 0.0   # neutral

        result = {
            "long_ratio": round(long_ratio, 4),
            "short_ratio": round(short_ratio, 4),
            "long_short_ratio": round(ls_ratio, 4),
            "open_interest": round(open_interest, 2),
            "crowded_long": crowded_long,
            "crowded_short": crowded_short,
            "positioning_signal": positioning_signal,
        }

        cache[binance_symbol] = result
        CACHE_PATH.write_text(json.dumps({
            "timestamp": time.time(),
            "data": cache
        }))
        return result

    except Exception as e:
        print(f"Positioning fetch failed for {symbol}: {e}")
        return {
            "long_ratio": 0.5, "short_ratio": 0.5,
            "long_short_ratio": 1.0, "open_interest": 0.0,
            "crowded_long": 0.0, "crowded_short": 0.0,
            "positioning_signal": 0.0,
        }
