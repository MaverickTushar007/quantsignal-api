from app.core.config import BASE_DIR
"""
data/positioning.py
OKX futures positioning — Long/Short ratio + Open Interest.
OKX works on Railway (not geo-blocked). Free, no API key.
Cached 15 minutes.
"""
import requests
import json
import time
from pathlib import Path

CACHE_PATH = BASE_DIR / "data/positioning_cache.json"
CACHE_TTL = 900  # 15 minutes

OKX_SYMBOL_MAP = {
    "BTC-USD": ("BTC", "BTC-USDT-SWAP"),
    "ETH-USD": ("ETH", "ETH-USDT-SWAP"),
    "SOL-USD": ("SOL", "SOL-USDT-SWAP"),
    "BNB-USD": ("BNB", "BNB-USDT-SWAP"),
    "XRP-USD": ("XRP", "XRP-USDT-SWAP"),
    "DOGE-USD": ("DOGE", "DOGE-USDT-SWAP"),
    "ADA-USD": ("ADA", "ADA-USDT-SWAP"),
    "AVAX-USD": ("AVAX", "AVAX-USDT-SWAP"),
    "DOT-USD": ("DOT", "DOT-USDT-SWAP"),
    "LINK-USD": ("LINK", "LINK-USDT-SWAP"),
    "LTC-USD": ("LTC", "LTC-USDT-SWAP"),
    "ATOM-USD": ("ATOM", "ATOM-USDT-SWAP"),
    "NEAR-USD": ("NEAR", "NEAR-USDT-SWAP"),
    "OP-USD": ("OP", "OP-USDT-SWAP"),
    "INJ-USD": ("INJ", "INJ-USDT-SWAP"),
}

def get_positioning(symbol: str) -> dict:
    okx_ids = OKX_SYMBOL_MAP.get(symbol)
    if not okx_ids:
        return {
            "long_ratio": 0.5, "short_ratio": 0.5,
            "long_short_ratio": 1.0, "open_interest": 0.0,
            "crowded_long": 0.0, "crowded_short": 0.0,
            "positioning_signal": 0.0,
        }

    ccy, inst_id = okx_ids

    # Check cache
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
        # OKX Long/Short ratio
        ls_url = f"https://www.okx.com/api/v5/rubik/stat/contracts/long-short-account-ratio-contract?instId={inst_id}&period=1D&limit=1"
        ls_resp = requests.get(ls_url, timeout=8).json()
        ls_data = ls_resp.get("data", [])
        if not ls_data:
            raise ValueError("Empty L/S response")

        ls_ratio = float(ls_data[0][1])
        long_ratio = round(ls_ratio / (1 + ls_ratio), 4)
        short_ratio = round(1 - long_ratio, 4)

        # OKX Open Interest
        oi_url = f"https://www.okx.com/api/v5/public/open-interest?instType=SWAP&instId={inst_id}"
        oi_resp = requests.get(oi_url, timeout=8).json()
        oi_data = oi_resp.get("data", [])
        open_interest = float(oi_data[0].get("oiCcy", 0)) if oi_data else 0.0

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
            "long_ratio": long_ratio,
            "short_ratio": short_ratio,
            "long_short_ratio": round(ls_ratio, 4),
            "open_interest": round(open_interest, 2),
            "crowded_long": crowded_long,
            "crowded_short": crowded_short,
            "positioning_signal": positioning_signal,
        }

        cache[symbol] = result
        CACHE_PATH.write_text(json.dumps({"timestamp": time.time(), "data": cache}))
        print(f"OKX positioning for {symbol}: {long_ratio*100:.1f}% longs, OI={open_interest:,.0f}")
        return result

    except Exception as e:
        print(f"OKX positioning failed for {symbol}: {e}")
        return {
            "long_ratio": 0.5, "short_ratio": 0.5,
            "long_short_ratio": 1.0, "open_interest": 0.0,
            "crowded_long": 0.0, "crowded_short": 0.0,
            "positioning_signal": 0.0,
        }
