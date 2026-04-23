"""
allocator.py
Portfolio-level capital allocator.
Takes live signals across all tickers and outputs a single allocation plan.

Constraints:
- Max 25% capital per ticker
- Max 100% total deployed (long + short gross)
- Min confidence threshold (MEDIUM or above)
- Cash buffer: always keep 20% undeployed
"""
import logging
from typing import Optional
import pandas as pd

logger = logging.getLogger(__name__)

MAX_CAPITAL = 1.0          # 100% of portfolio
CASH_BUFFER = 0.20         # always keep 20% cash
MAX_PER_TICKER = 0.25      # max 25% per single position
MAX_GROSS = 0.80           # max 80% gross deployed (long + short abs)
MIN_CONFIDENCE = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
MIN_CONFIDENCE_THRESHOLD = 1  # MEDIUM or above


def allocate(
    capital: float = 1_000_000.0,
    tickers: Optional[list] = None,
    include_reasoning: bool = False,
) -> dict:
    """
    Main entry point. Fetches live signals and returns allocation plan.
    
    Args:
        capital: Total portfolio capital in base currency (default ₹10L)
        tickers: List of tickers to include. None = full universe.
        include_reasoning: Whether to include Perseus reasoning in signals.
    
    Returns dict with:
        - allocations: list of position dicts
        - summary: portfolio-level stats
        - raw_signals: the underlying signals used
    """
    from app.domain.data.universe import TICKER_MAP
    from app.domain.signal.service import generate_signal

    if tickers is None:
        tickers = list(TICKER_MAP.keys())

    # 1. Fetch all signals
    raw_signals = {}
    for ticker in tickers:
        try:
            sig = generate_signal(ticker, include_reasoning=include_reasoning)
            if sig:
                raw_signals[ticker] = sig
        except Exception as e:
            logger.warning(f"[allocator] {ticker} signal failed: {e}")

    if not raw_signals:
        return {"error": "No signals available", "allocations": [], "summary": {}}

    # 2. Filter by confidence
    qualified = {
        t: s for t, s in raw_signals.items()
        if MIN_CONFIDENCE.get(str(s.get("confidence", "LOW")).upper(), 0) >= MIN_CONFIDENCE_THRESHOLD
        and s.get("direction", "HOLD") != "HOLD"
        and abs(float(s.get("kelly_size", 0))) > 0
    }

    if not qualified:
        return {
            "allocations": [],
            "summary": {
                "total_tickers_scanned": len(raw_signals),
                "qualified_signals": 0,
                "total_deployed_pct": 0.0,
                "net_exposure_pct": 0.0,
                "cash_pct": 100.0,
                "capital": capital,
            },
            "raw_signals": raw_signals,
        }

    # 3. Build raw allocation weights from Kelly sizes
    allocations = []
    for ticker, sig in qualified.items():
        kelly_raw = float(sig.get("kelly_size", 0)) / 100.0  # kelly_size is in %, convert to fraction
        direction = sig.get("direction", "HOLD")
        
        # Cap per-ticker
        weight = max(min(abs(kelly_raw), MAX_PER_TICKER), 0)
        signed_weight = weight if direction == "BUY" else -weight

        allocations.append({
            "ticker":      ticker,
            "direction":   direction,
            "probability": float(sig.get("probability", 0.5)),
            "confidence":  str(sig.get("confidence", "LOW")),
            "kelly_raw":   round(kelly_raw * 100, 2),
            "weight":      round(signed_weight, 4),
            "weight_pct":  round(signed_weight * 100, 2),
        })

    # 4. Sort by abs weight descending (highest conviction first)
    allocations.sort(key=lambda x: abs(x["weight"]), reverse=True)

    # 5. Apply gross exposure cap — scale down if total > MAX_GROSS
    gross = sum(abs(a["weight"]) for a in allocations)
    deployable = MAX_CAPITAL - CASH_BUFFER  # 80%

    if gross > deployable:
        scale = deployable / gross
        for a in allocations:
            a["weight"]     = round(a["weight"] * scale, 4)
            a["weight_pct"] = round(a["weight_pct"] * scale, 2)

    # 6. Compute rupee amounts and final stats
    total_long = 0.0
    total_short = 0.0

    for a in allocations:
        a["allocated_pct"]    = a["weight_pct"]
        a["allocated_amount"] = round(capital * abs(a["weight"]), 2)
        a["signed_amount"]    = round(capital * a["weight"], 2)
        if a["direction"] == "BUY":
            total_long  += abs(a["weight"])
        else:
            total_short += abs(a["weight"])

    gross_deployed  = total_long + total_short
    net_exposure    = total_long - total_short
    cash_pct        = max(1.0 - gross_deployed, 0.0)

    summary = {
        "total_tickers_scanned": len(raw_signals),
        "qualified_signals":     len(allocations),
        "long_positions":        sum(1 for a in allocations if a["direction"] == "BUY"),
        "short_positions":       sum(1 for a in allocations if a["direction"] == "SELL"),
        "total_long_pct":        round(total_long * 100, 2),
        "total_short_pct":       round(total_short * 100, 2),
        "gross_deployed_pct":    round(gross_deployed * 100, 2),
        "net_exposure_pct":      round(net_exposure * 100, 2),
        "cash_pct":              round(cash_pct * 100, 2),
        "capital":               capital,
        "deployed_amount":       round(capital * gross_deployed, 2),
        "cash_amount":           round(capital * cash_pct, 2),
    }

    return {
        "allocations": allocations,
        "summary":     summary,
        "raw_signals": {t: {
            "direction":   s.get("direction"),
            "probability": s.get("probability"),
            "confidence":  s.get("confidence"),
            "kelly_size":  s.get("kelly_size"),
        } for t, s in raw_signals.items()},
    }


def allocate_from_cache(cached_signals: dict, capital: float = 1_000_000.0) -> dict:
    """
    Build portfolio allocation from pre-cached signals dict.
    Fast path — no ML inference, reads existing signal data.
    
    cached_signals: dict of {ticker: signal_dict} or list of signal dicts
    """
    # Normalize input — cache may be list or dict
    if isinstance(cached_signals, list):
        raw_signals = {s["symbol"]: s for s in cached_signals if "symbol" in s}
    elif isinstance(cached_signals, dict):
        # May be nested under a key
        if "signals" in cached_signals:
            raw_signals = {s["symbol"]: s for s in cached_signals["signals"] if "symbol" in s}
        else:
            raw_signals = cached_signals
    else:
        return {"error": "Invalid cache format", "allocations": [], "summary": {}}

    if not raw_signals:
        return {"error": "Empty cache", "allocations": [], "summary": {}}

    # Filter qualified signals
    qualified = {}
    for ticker, sig in raw_signals.items():
        direction = str(sig.get("direction", "HOLD"))
        confidence = str(sig.get("confidence", "LOW")).upper()
        kelly = float(sig.get("kelly_size", 0) or 0)

        if (direction != "HOLD"
                and MIN_CONFIDENCE.get(confidence, 0) >= MIN_CONFIDENCE_THRESHOLD
                and abs(kelly) > 0):
            qualified[ticker] = sig

    if not qualified:
        return {
            "allocations": [],
            "summary": {
                "total_tickers_scanned": len(raw_signals),
                "qualified_signals": 0,
                "total_deployed_pct": 0.0,
                "net_exposure_pct": 0.0,
                "cash_pct": 100.0,
                "capital": capital,
            },
        }

    # Build allocations
    allocations = []
    for ticker, sig in qualified.items():
        kelly_raw = float(sig.get("kelly_size", 0)) / 100.0
        direction = sig.get("direction")
        weight = max(min(abs(kelly_raw), MAX_PER_TICKER), 0)
        signed_weight = weight if direction == "BUY" else -weight

        allocations.append({
            "ticker":      ticker,
            "direction":   direction,
            "probability": float(sig.get("probability", 0.5)),
            "confidence":  str(sig.get("confidence", "LOW")),
            "kelly_raw":   round(kelly_raw * 100, 2),
            "weight":      round(signed_weight, 4),
            "weight_pct":  round(signed_weight * 100, 2),
        })

    allocations.sort(key=lambda x: abs(x["weight"]), reverse=True)

    # Scale to gross cap
    gross = sum(abs(a["weight"]) for a in allocations)
    deployable = MAX_CAPITAL - CASH_BUFFER
    if gross > deployable:
        scale = deployable / gross
        for a in allocations:
            a["weight"]     = round(a["weight"] * scale, 4)
            a["weight_pct"] = round(a["weight_pct"] * scale, 2)

    total_long, total_short = 0.0, 0.0
    for a in allocations:
        a["allocated_pct"]    = a["weight_pct"]
        a["allocated_amount"] = round(capital * abs(a["weight"]), 2)
        a["signed_amount"]    = round(capital * a["weight"], 2)
        if a["direction"] == "BUY":
            total_long  += abs(a["weight"])
        else:
            total_short += abs(a["weight"])

    gross_deployed = total_long + total_short
    net_exposure   = total_long - total_short
    cash_pct       = max(1.0 - gross_deployed, 0.0)

    return {
        "allocations": allocations,
        "summary": {
            "total_tickers_scanned": len(raw_signals),
            "qualified_signals":     len(allocations),
            "long_positions":        sum(1 for a in allocations if a["direction"] == "BUY"),
            "short_positions":       sum(1 for a in allocations if a["direction"] == "SELL"),
            "total_long_pct":        round(total_long * 100, 2),
            "total_short_pct":       round(total_short * 100, 2),
            "gross_deployed_pct":    round(gross_deployed * 100, 2),
            "net_exposure_pct":      round(net_exposure * 100, 2),
            "cash_pct":              round(cash_pct * 100, 2),
            "capital":               capital,
            "deployed_amount":       round(capital * gross_deployed, 2),
            "cash_amount":           round(capital * cash_pct, 2),
        },
    }
