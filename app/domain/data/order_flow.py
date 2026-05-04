"""
order_flow.py — Order Flow Imbalance (OFI) features for QuantSignal

Academic basis:
  - "Returns and Order Flow Imbalances: Intraday Evidence" — arXiv 2508.06788
    https://arxiv.org/abs/2508.06788
  - "Order Flow Imbalance — A High Frequency Trading Signal" — dm13450:
    https://dm13450.github.io/2022/02/02/Order-Flow-Imbalance.html
  - "How to Use Deep Order Flow Imbalance" — QuantPedia:
    https://quantpedia.com/how-to-use-deep-order-flow-imbalance/
  - Deep Learning OFI project (CNN+LSTM on Coinbase BTC LOB):
    https://github.com/ajcutuli/OFI_NN_Project
  - LOBSTER LOB Reconstruction:
    https://data.lobsterdata.com/info/docs/LobsterReport.pdf

What it does:
  OFI = sum over N levels of:
    bid_size[level] * (bid_price_changed_up) 
    - ask_size[level] * (ask_price_changed_down)

  Positive OFI → more aggressive buying → price likely to rise
  Negative OFI → more aggressive selling → price likely to fall

Data source:
  OKX REST API (you already have OKX integration):
    GET /api/v5/market/books?instId={symbol}&sz=20
  Returns top 20 bid/ask levels — enough for deep OFI (5-level used here)

Features exported:
  ofi_1           : single-snapshot OFI (current imbalance)
  ofi_rolling_5   : rolling 5-snapshot OFI (smoothed)
  ofi_signal      : +1 (bullish), -1 (bearish), 0 (neutral)
  bid_ask_spread  : (best_ask - best_bid) / mid_price (liquidity proxy)
  depth_imbalance : (total_bid_depth - total_ask_depth) / total_depth
"""

from __future__ import annotations
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# ── OKX API config ────────────────────────────────────────────────────────────
OKX_BASE = "https://www.okx.com"
OKX_BOOKS_ENDPOINT = "/api/v5/market/books"
OFI_LEVELS = 5          # number of LOB levels for deep OFI
OFI_CACHE_SEC = 30      # cache OFI for 30s to avoid hammering API

# ── cache ────────────────────────────────────────────────────────────────────
_ofi_cache: dict[str, dict] = {}


# ── OKX symbol normalizer ─────────────────────────────────────────────────────
_SYMBOL_MAP = {
    "BTC-USD":   "BTC-USDT",
    "ETH-USD":   "ETH-USDT",
    "SOL-USD":   "SOL-USDT",
    "BNB-USD":   "BNB-USDT",
    "XRP-USD":   "XRP-USDT",
    "DOGE-USD":  "DOGE-USDT",
    "ADA-USD":   "ADA-USDT",
    "AVAX-USD":  "AVAX-USDT",
    "DOT-USD":   "DOT-USDT",
    "MATIC-USD": "MATIC-USDT",
}

def _to_okx_symbol(symbol: str) -> Optional[str]:
    """Convert yfinance-style symbol to OKX instId format."""
    return _SYMBOL_MAP.get(symbol)


# ── LOB fetcher ───────────────────────────────────────────────────────────────
def _fetch_order_book(okx_symbol: str, depth: int = 20) -> Optional[dict]:
    """
    Fetch order book from OKX REST API.
    Returns dict with 'bids' and 'asks' lists of [price, size, _, _].
    Ref: https://www.okx.com/docs-v5/en/#order-book-trading-market-data-get-order-book
    """
    try:
        url    = f"{OKX_BASE}{OKX_BOOKS_ENDPOINT}"
        params = {"instId": okx_symbol, "sz": str(depth)}
        resp   = requests.get(url, params=params, timeout=5)
        data   = resp.json()
        if data.get("code") != "0":
            logger.warning(f"[ofi] OKX API error: {data.get('msg')}")
            return None
        book = data["data"][0]
        return {
            "bids": [[float(b[0]), float(b[1])] for b in book["bids"]],
            "asks": [[float(a[0]), float(a[1])] for a in book["asks"]],
            "ts":   int(book.get("ts", time.time() * 1000)),
        }
    except Exception as e:
        logger.warning(f"[ofi] fetch failed for {okx_symbol}: {e}")
        return None


# ── OFI computation ───────────────────────────────────────────────────────────
def _compute_ofi(book_t0: dict, book_t1: dict, levels: int = OFI_LEVELS) -> float:
    """
    Compute Order Flow Imbalance between two consecutive LOB snapshots.

    OFI_n = sum_{i=1}^{N} [
        bid_size_t1[i] * 1(bid_price_t1[i] >= bid_price_t0[i])
      - ask_size_t1[i] * 1(ask_price_t1[i] <= ask_price_t0[i])
    ]

    Positive → net buying pressure
    Negative → net selling pressure

    Ref: arXiv 2508.06788, dm13450 OFI blog post
    """
    ofi = 0.0
    for i in range(min(levels, len(book_t0["bids"]), len(book_t1["bids"]))):
        bp0, bs0 = book_t0["bids"][i]
        bp1, bs1 = book_t1["bids"][i]
        ap0, as0 = book_t0["asks"][i]
        ap1, as1 = book_t1["asks"][i]
        # Bid side: buy pressure if bid price moved up or same
        if bp1 >= bp0:
            ofi += bs1
        else:
            ofi -= bs1
        # Ask side: sell pressure if ask price moved down or same
        if ap1 <= ap0:
            ofi -= as1
        else:
            ofi += as1
    return float(ofi)


def _compute_depth_imbalance(book: dict, levels: int = 10) -> float:
    """
    Depth imbalance = (total bid depth - total ask depth) / total depth
    Range: -1 (all asks) to +1 (all bids)
    Ref: QuantPedia deep OFI guide
    """
    bids = book["bids"][:levels]
    asks = book["asks"][:levels]
    bid_depth = sum(b[1] for b in bids)
    ask_depth = sum(a[1] for a in asks)
    total = bid_depth + ask_depth
    if total == 0:
        return 0.0
    return (bid_depth - ask_depth) / total


# ── main public API ───────────────────────────────────────────────────────────
def get_ofi_features(symbol: str) -> dict:
    """
    Returns OFI-derived features for a crypto symbol.

    For non-crypto symbols (equities, forex) returns neutral defaults
    since OKX only covers crypto.

    Returns:
      ofi_1           : float — current OFI (positive=bullish, negative=bearish)
      ofi_signal      : int   — +1, -1, or 0
      bid_ask_spread  : float — (best_ask - best_bid) / mid_price
      depth_imbalance : float — bid depth - ask depth fraction
      available       : bool  — False for non-crypto symbols
    """
    okx_sym = _to_okx_symbol(symbol)
    if not okx_sym:
        return {
            "ofi_1": 0.0, "ofi_signal": 0,
            "bid_ask_spread": 0.0, "depth_imbalance": 0.0,
            "available": False,
        }

    # Check cache
    cached = _ofi_cache.get(symbol)
    if cached and (time.time() - cached.get("cached_at", 0)) < OFI_CACHE_SEC:
        return cached

    # Two snapshots 2s apart for OFI delta
    book_t0 = _fetch_order_book(okx_sym, depth=20)
    if book_t0 is None:
        return {"ofi_1": 0.0, "ofi_signal": 0, "bid_ask_spread": 0.0,
                "depth_imbalance": 0.0, "available": False}

    time.sleep(2)   # wait for next snapshot
    book_t1 = _fetch_order_book(okx_sym, depth=20)
    if book_t1 is None:
        book_t1 = book_t0   # fallback: use same book twice → OFI=0

    ofi = _compute_ofi(book_t0, book_t1)
    depth_imb = _compute_depth_imbalance(book_t1)

    best_bid = book_t1["bids"][0][0] if book_t1["bids"] else 0
    best_ask = book_t1["asks"][0][0] if book_t1["asks"] else 0
    mid      = (best_bid + best_ask) / 2
    spread   = (best_ask - best_bid) / mid if mid > 0 else 0.0

    # Signal: normalize OFI by total depth to get -1/0/+1
    total_depth = sum(b[1] for b in book_t1["bids"][:OFI_LEVELS]) + \
                  sum(a[1] for a in book_t1["asks"][:OFI_LEVELS])
    ofi_norm = ofi / total_depth if total_depth > 0 else 0.0

    if ofi_norm > 0.10:
        ofi_signal = 1
    elif ofi_norm < -0.10:
        ofi_signal = -1
    else:
        ofi_signal = 0

    result = {
        "ofi_1":           round(ofi, 4),
        "ofi_norm":        round(ofi_norm, 4),
        "ofi_signal":      ofi_signal,
        "bid_ask_spread":  round(spread, 6),
        "depth_imbalance": round(depth_imb, 4),
        "best_bid":        best_bid,
        "best_ask":        best_ask,
        "available":       True,
        "cached_at":       time.time(),
        "symbol":          symbol,
    }
    _ofi_cache[symbol] = result
    return result


def ofi_confluence_score(symbol: str, direction: str) -> float:
    """
    Returns confluence score contribution (0.0 or 1.0) for the scorecard.
    Only for crypto symbols with OKX data.
    """
    feat = get_ofi_features(symbol)
    if not feat.get("available"):
        return 0.5   # neutral for non-crypto
    sig = feat["ofi_signal"]
    if direction == "BUY"  and sig == 1:  return 1.0
    if direction == "SELL" and sig == -1: return 1.0
    return 0.0
