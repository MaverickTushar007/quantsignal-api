"""
data/positioning.py
Bybit futures positioning — Long/Short ratio + Open Interest.
Bybit is not geo-restricted on Railway. Free, no API key required.
Cached 15 minutes.
"""
import requests
import json
import time
from pathlib import Path

CACHE_PATH = Path("data/positioning_cache.json")
CACHE_TTL = 900  # 15 minutes

BYBIT_SYMBOL_MAP = {
    "BTC-USD": "BTCUSDT", "ETH-USD": "ETHUSDT", "SOL-USD": "SOLUSDT",
    "BNB-USD": "BNBUSDT", "XRP-USD": "XRPUSDT", "DOGE-USD": "DOGEUSDT",
    "ADA-USD": "ADAUSDT", "AVAX-USD": "AVAXUSDT", "MATIC-USD": "MATICUSDT",
    "DOT-USD": "DOTUSDT", "LINK-USD": "LINKUSDT", "LTC-USD": "LTCUSDT",
    "ATOM-USD": "ATOMUSDT", "NEAR-USD": "NEARUSDT", "OP-USD": "OPUSDT",
    "INJ-USD": "INJUSDT", "FET-USD": "FETUSDT",
}

def get_positioning(symbol: str) -> dict:
    bybit_symbol = BYBIT_SYMBOL_MAP.get(symbol)
    if not bybit_symbol:
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
                if bybit_symbol in cached.get("data", {}):
                    return cached["data"][bybit_symbol]
            cache = cached.get("data", {})
        except Exception:
            pass

    try:
        # Bybit Long/Short ratio
        ls_url = f"https://api.bybit.com/v5/market/account-ratio?category=linear&symbol={bybit_symbol}&period=1d&limit=1"
        ls_resp = requests.get(ls_url, timeout=8).json()
        ls_list = ls_resp.get("result", {}).get("list", [])
        if not ls_list:
            raise ValueError("Empty L/S response")

        long_ratio = float(ls_list[0]["buyRatio"])
        short_ratio = float(ls_list[0]["sellRatio"])
        ls_ratio = round(long_ratio / short_ratio, 4) if short_ratio > 0 else 1.0

        # Bybit Open Interest
        oi_url = f"https://api.bybit.com/v5/market/open-interest?category=linear&symbol={bybit_symbol}&intervalTime=1d&limit=1"
        oi_resp = requests.get(oi_url, timeout=8).json()
        oi_list = oi_resp.get("result", {}).get("list", [])
        open_interest = float(oi_list[0]["openInterest"]) if oi_list else 0.0

        # Signals
        crowded_long = 1.0 if long_ratio > 0.65 else 0.0
        crowded_short = 1.0 if long_ratio < 0.35 else 0.0

        if long_ratio > 0.65:
            positioning_signal = -1.0
        elif long_ratio < 0.35:
            positioning_signal = 1.0
        elif long_ratio > 0.58:
            positioning_signal = -0.5
        elif long_ratio < 0.42:
            positioning_signal = 0.5
        else:
            positioning_signal = 0.0

        result = {
            "long_ratio": round(long_ratio, 4),
            "short_ratio": round(short_ratio, 4),
            "long_short_ratio": round(ls_ratio, 4),
            "open_interest": round(open_interest, 2),
            "crowded_long": crowded_long,
            "crowded_short": crowded_short,
            "positioning_signal": positioning_signal,
        }

        cache[bybit_symbol] = result
        CACHE_PATH.write_text(json.dumps({"timestamp": time.time(), "data": cache}))
        print(f"Bybit positioning for {symbol}: {long_ratio*100:.1f}% longs, OI={open_interest:,.0f}")
        return result

    except Exception as e:
        print(f"Bybit positioning failed for {symbol}: {e}")
        return {
            "long_ratio": 0.5, "short_ratio": 0.5,
            "long_short_ratio": 1.0, "open_interest": 0.0,
            "crowded_long": 0.0, "crowded_short": 0.0,
            "positioning_signal": 0.0,
        }
