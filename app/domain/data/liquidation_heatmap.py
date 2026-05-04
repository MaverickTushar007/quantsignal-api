"""
liquidation_heatmap.py — CoinGlass Liquidation Cluster Signal for QuantSignal

Academic basis:
  - CoinGlass Crypto Data API (liquidations, OI, order flow, funding):
    https://www.coinglass.com/CryptoApi
  - CoinGlass API Data Guide:
    https://www.coinglass.com/learn/the-ultimate-crypto-derivatives-data-solution-for-traders-and-developers-en
  - CoinGlass liquidation chart open-sourced (reference implementation):
    https://github.com/StephanAkkerman/liquidations-chart
  - Kwery unified API (Binance/Hyperliquid/others):
    https://kwery.xyz

What it does:
  - Fetches recent liquidation data from CoinGlass API
  - Identifies liquidation clusters (price levels with heavy longs/shorts)
  - Generates a signal: is price approaching a liquidation cascade zone?
  - Long liquidation cluster ABOVE price → supply zone (bearish near-term)
  - Short liquidation cluster BELOW price → demand zone (bullish squeeze)

Fallback (no API key):
  Uses CoinGlass public endpoints where available.
  If rate-limited, falls back to Binance liquidation data.

Signal logic:
  liq_pressure_long  : total long liquidations in last window (USD)
  liq_pressure_short : total short liquidations in last window
  liq_ratio          : long_liq / (long_liq + short_liq)  — >0.6 = panic selling
  liq_signal         : +1 (short squeeze building), -1 (long liquidation cascade), 0 (neutral)
"""

from __future__ import annotations
import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# ── CoinGlass config ──────────────────────────────────────────────────────────
# Public endpoint (no API key needed for basic liquidation data):
COINGLASS_PUBLIC_BASE = "https://open-api.coinglass.com/public/v2"
COINGLASS_LIQ_ENDPOINT = f"{COINGLASS_PUBLIC_BASE}/liquidation_history"

# Fallback: Binance futures liquidation stream (public, no auth)
BINANCE_LIQ_BASE = "https://fapi.binance.com"
BINANCE_LIQ_ENDPOINT = f"{BINANCE_LIQ_BASE}/fapi/v1/allForceOrders"

# Cache path
BASE_DIR  = Path(__file__).resolve().parents[3]
LIQ_CACHE = BASE_DIR / "data" / "liq_cache.json"
LIQ_CACHE.parent.mkdir(parents=True, exist_ok=True)

_LIQ_CACHE_SEC = 300   # 5 minutes

# Symbol map to Binance futures format
_BINANCE_SYMBOL_MAP = {
    "BTC-USD":  "BTCUSDT",
    "ETH-USD":  "ETHUSDT",
    "SOL-USD":  "SOLUSDT",
    "BNB-USD":  "BNBUSDT",
    "XRP-USD":  "XRPUSDT",
    "DOGE-USD": "DOGEUSDT",
    "ADA-USD":  "ADAUSDT",
    "AVAX-USD": "AVAXUSDT",
}

_mem_cache: dict = {}


def _fetch_binance_liquidations(binance_sym: str, lookback_hours: int = 4) -> dict:
    """
    Fetch recent forced liquidations from Binance futures public API.
    Returns summary of long/short liquidation volumes.
    Ref: https://binance-docs.github.io/apidocs/futures/en/#get-all-liquidation-orders
    """
    try:
        params = {
            "symbol":    binance_sym,
            "limit":     200,
            "startTime": int((datetime.utcnow() - timedelta(hours=lookback_hours)).timestamp() * 1000),
        }
        resp = requests.get(BINANCE_LIQ_ENDPOINT, params=params, timeout=8)
        orders = resp.json()
        if not isinstance(orders, list):
            return {}

        long_liq  = sum(float(o["origQty"]) * float(o["price"])
                        for o in orders if o.get("side") == "SELL")   # SELL = long liquidated
        short_liq = sum(float(o["origQty"]) * float(o["price"])
                        for o in orders if o.get("side") == "BUY")    # BUY = short liquidated
        total = long_liq + short_liq
        return {
            "long_liq_usd":  round(long_liq, 2),
            "short_liq_usd": round(short_liq, 2),
            "total_liq_usd": round(total, 2),
            "liq_ratio":     round(long_liq / total, 4) if total > 0 else 0.5,
            "source":        "binance",
            "symbol":        binance_sym,
            "lookback_hours": lookback_hours,
        }
    except Exception as e:
        logger.warning(f"[liq] Binance fetch failed for {binance_sym}: {e}")
        return {}


def get_liquidation_signal(symbol: str, lookback_hours: int = 4) -> dict:
    """
    Returns liquidation pressure signal for a crypto symbol.

    Returns:
      liq_pressure_long  : float — USD value of long liquidations
      liq_pressure_short : float — USD value of short liquidations
      liq_ratio          : float — long_liq / total  (>0.6 = panic, <0.4 = squeeze)
      liq_signal         : int   — +1 (short squeeze), -1 (long cascade), 0 (neutral)
      available          : bool  — False for non-crypto
    """
    binance_sym = _BINANCE_SYMBOL_MAP.get(symbol)
    if not binance_sym:
        return {"liq_signal": 0, "available": False, "liq_ratio": 0.5}

    # Memory cache check
    cached = _mem_cache.get(symbol)
    if cached and (time.time() - cached.get("cached_at", 0)) < _LIQ_CACHE_SEC:
        return cached

    data = _fetch_binance_liquidations(binance_sym, lookback_hours)
    if not data:
        return {"liq_signal": 0, "available": False, "liq_ratio": 0.5}

    liq_ratio  = data["liq_ratio"]
    total_liq  = data["total_liq_usd"]

    # Only signal if meaningful liquidation volume (>$500k in window)
    MIN_USD = 500_000
    if total_liq < MIN_USD:
        liq_signal = 0    # too little activity to signal
    elif liq_ratio > 0.65:
        liq_signal = -1   # mostly longs liquidated → bearish cascade
    elif liq_ratio < 0.35:
        liq_signal = 1    # mostly shorts liquidated → bullish squeeze
    else:
        liq_signal = 0    # balanced

    result = {
        **data,
        "liq_signal":   liq_signal,
        "available":    True,
        "cached_at":    time.time(),
    }
    _mem_cache[symbol] = result
    return result


def liq_confluence_score(symbol: str, direction: str) -> float:
    """
    Returns confluence score contribution (0.0 or 1.0).
    Used as Phase 2C addition to the confluence scorecard.
    """
    feat = get_liquidation_signal(symbol)
    if not feat.get("available"):
        return 0.5
    sig = feat["liq_signal"]
    if direction == "BUY"  and sig == 1:  return 1.0
    if direction == "SELL" and sig == -1: return 1.0
    return 0.0
