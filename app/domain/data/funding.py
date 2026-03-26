"""
data/funding.py
OKX perpetual futures funding rates.
OKX works on Railway (not geo-blocked). Free, no API key.
Cached 1 hour.
"""
import requests
import json
import time
from pathlib import Path

CACHE_PATH = Path("data/funding_cache.json")
CACHE_TTL = 3600

OKX_INST_MAP = {
    "BTC-USD": "BTC-USDT-SWAP", "ETH-USD": "ETH-USDT-SWAP",
    "SOL-USD": "SOL-USDT-SWAP", "BNB-USD": "BNB-USDT-SWAP",
    "XRP-USD": "XRP-USDT-SWAP", "DOGE-USD": "DOGE-USDT-SWAP",
    "ADA-USD": "ADA-USDT-SWAP", "AVAX-USD": "AVAX-USDT-SWAP",
    "DOT-USD": "DOT-USDT-SWAP", "LINK-USD": "LINK-USDT-SWAP",
    "LTC-USD": "LTC-USDT-SWAP", "ATOM-USD": "ATOM-USDT-SWAP",
    "NEAR-USD": "NEAR-USDT-SWAP", "OP-USD": "OP-USDT-SWAP",
    "INJ-USD": "INJ-USDT-SWAP",
}

def get_funding_features(symbol: str) -> dict:
    inst_id = OKX_INST_MAP.get(symbol)
    if not inst_id:
        return {"funding_rate": 0.0, "funding_signal": 0.0,
                "is_overleveraged_long": 0.0, "is_overleveraged_short": 0.0}

    cache = {}
    if CACHE_PATH.exists():
        try:
            cached = json.loads(CACHE_PATH.read_text())
            if time.time() - cached.get("timestamp", 0) < CACHE_TTL:
                if symbol in cached.get("data", {}):
                    return cached["data"][symbol]
            cache = cached.get("data", {})
        except Exception:
            pass

    try:
        url = f"https://www.okx.com/api/v5/public/funding-rate?instId={inst_id}"
        resp = requests.get(url, timeout=8).json()
        data = resp.get("data", [])
        if not data:
            raise ValueError("Empty funding response")

        latest_rate = float(data[0]["fundingRate"])

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

        cache[symbol] = result
        CACHE_PATH.write_text(json.dumps({"timestamp": time.time(), "data": cache}))
        print(f"OKX funding for {symbol}: {latest_rate*100:.4f}%")
        return result

    except Exception as e:
        print(f"OKX funding failed for {symbol}: {e}")
        return {"funding_rate": 0.0, "funding_signal": 0.0,
                "is_overleveraged_long": 0.0, "is_overleveraged_short": 0.0}
