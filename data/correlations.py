"""
data/correlations.py
Cross-asset correlation map + shock scanner.
When one asset moves sharply, flags correlated assets with a warning.
"""
import json
import time
from pathlib import Path

# Hardcoded correlation map — reliable, no API needed
CORRELATION_MAP = {
    # Indian IT
    "INFY.NS":        ["TCS.NS", "WIPRO.NS", "HCLTECH.NS", "MPHASIS.NS", "LTIM.NS", "COFORGE.NS"],
    "TCS.NS":         ["INFY.NS", "WIPRO.NS", "HCLTECH.NS", "MPHASIS.NS", "LTIM.NS"],
    "WIPRO.NS":       ["INFY.NS", "TCS.NS", "HCLTECH.NS", "MPHASIS.NS"],
    "HCLTECH.NS":     ["INFY.NS", "TCS.NS", "WIPRO.NS", "LTIM.NS"],
    "MPHASIS.NS":     ["INFY.NS", "TCS.NS", "WIPRO.NS", "PERSISTENT.NS"],
    "LTIM.NS":        ["INFY.NS", "TCS.NS", "HCLTECH.NS", "COFORGE.NS"],

    # Indian Banking
    "HDFCBANK.NS":    ["ICICIBANK.NS", "AXISBANK.NS", "KOTAKBANK.NS", "SBIN.NS", "BANKBARODA.NS"],
    "ICICIBANK.NS":   ["HDFCBANK.NS", "AXISBANK.NS", "KOTAKBANK.NS", "SBIN.NS"],
    "AXISBANK.NS":    ["HDFCBANK.NS", "ICICIBANK.NS", "KOTAKBANK.NS"],
    "KOTAKBANK.NS":   ["HDFCBANK.NS", "ICICIBANK.NS", "AXISBANK.NS"],
    "SBIN.NS":        ["HDFCBANK.NS", "ICICIBANK.NS", "BANKBARODA.NS", "PNB.NS"],

    # Indian Energy
    "RELIANCE.NS":    ["ONGC.NS", "IOC.NS", "BPCL.NS", "GAIL.NS"],
    "ONGC.NS":        ["RELIANCE.NS", "IOC.NS", "BPCL.NS"],
    "BPCL.NS":        ["RELIANCE.NS", "ONGC.NS", "IOC.NS"],

    # Indian Metals
    "TATASTEEL.NS":   ["HINDALCO.NS", "JSWSTEEL.NS", "SAIL.NS", "VEDL.NS"],
    "HINDALCO.NS":    ["TATASTEEL.NS", "JSWSTEEL.NS", "VEDL.NS"],
    "JSWSTEEL.NS":    ["TATASTEEL.NS", "HINDALCO.NS", "SAIL.NS"],

    # Indian Auto
    "MARUTI.NS":      ["TATAMOTORS.NS", "M&M.NS", "BAJAJ-AUTO.NS", "HEROMOTOCO.NS"],
    "TATAMOTORS.NS":  ["MARUTI.NS", "M&M.NS", "BAJAJ-AUTO.NS"],
    "M&M.NS":         ["MARUTI.NS", "TATAMOTORS.NS", "BAJAJ-AUTO.NS"],

    # Indian Pharma
    "SUNPHARMA.NS":   ["DRREDDY.NS", "CIPLA.NS", "DIVISLAB.NS", "LUPIN.NS"],
    "DRREDDY.NS":     ["SUNPHARMA.NS", "CIPLA.NS", "LUPIN.NS"],
    "CIPLA.NS":       ["SUNPHARMA.NS", "DRREDDY.NS", "DIVISLAB.NS"],

    # Crypto
    "BTC-USD":        ["ETH-USD", "SOL-USD", "BNB-USD", "AVAX-USD", "MATIC-USD"],
    "ETH-USD":        ["BTC-USD", "SOL-USD", "BNB-USD", "AVAX-USD"],
    "SOL-USD":        ["BTC-USD", "ETH-USD", "AVAX-USD"],
    "BNB-USD":        ["BTC-USD", "ETH-USD", "SOL-USD"],
    "AVAX-USD":       ["BTC-USD", "ETH-USD", "SOL-USD"],

    # US Tech
    "AAPL":           ["MSFT", "GOOGL", "META", "NVDA"],
    "NVDA":           ["AMD", "AAPL", "MSFT", "INTC"],
    "MSFT":           ["AAPL", "GOOGL", "META", "NVDA"],
    "GOOGL":          ["MSFT", "META", "AAPL"],
    "META":           ["GOOGL", "MSFT", "AAPL"],
}

# Sector labels for human-readable warnings
SECTOR_LABELS = {
    "INFY.NS": "IT", "TCS.NS": "IT", "WIPRO.NS": "IT",
    "HCLTECH.NS": "IT", "MPHASIS.NS": "IT", "LTIM.NS": "IT",
    "HDFCBANK.NS": "Banking", "ICICIBANK.NS": "Banking",
    "AXISBANK.NS": "Banking", "KOTAKBANK.NS": "Banking", "SBIN.NS": "Banking",
    "RELIANCE.NS": "Energy", "ONGC.NS": "Energy", "BPCL.NS": "Energy",
    "TATASTEEL.NS": "Metals", "HINDALCO.NS": "Metals", "JSWSTEEL.NS": "Metals",
    "MARUTI.NS": "Auto", "TATAMOTORS.NS": "Auto", "M&M.NS": "Auto",
    "SUNPHARMA.NS": "Pharma", "DRREDDY.NS": "Pharma", "CIPLA.NS": "Pharma",
    "BTC-USD": "Crypto", "ETH-USD": "Crypto", "SOL-USD": "Crypto",
    "BNB-USD": "Crypto", "AVAX-USD": "Crypto",
    "AAPL": "US Tech", "NVDA": "US Tech", "MSFT": "US Tech",
    "GOOGL": "US Tech", "META": "US Tech",
}


def scan_for_shocks(cache: dict, threshold_pct: float = 3.0) -> dict:
    """
    Scans all signals for large price moves.
    Returns {symbol: shock_warning} for affected neighbors.
    """
    import yfinance as yf

    shocked = {}     # assets that moved sharply
    warnings = {}    # neighbors that should be flagged

    # Check each asset for large 1-day move
    symbols_to_check = list(CORRELATION_MAP.keys())

    for sym in symbols_to_check:
        try:
            ticker = yf.Ticker(sym)
            fi = ticker.fast_info
            day_change = fi.year_change  # approximate — use last close vs prev close
            # Better: use regularMarketChangePercent
            info = ticker.info
            change_pct = info.get('regularMarketChangePercent')
            if change_pct is None:
                continue
            if abs(change_pct) >= threshold_pct:
                shocked[sym] = round(change_pct, 2)
        except Exception:
            continue

    # For each shocked asset, flag its neighbors
    for sym, move in shocked.items():
        neighbors = CORRELATION_MAP.get(sym, [])
        sector = SECTOR_LABELS.get(sym, "sector")
        direction = "fell" if move < 0 else "surged"
        sign = "+" if move > 0 else ""

        for neighbor in neighbors:
            if neighbor not in warnings:
                warnings[neighbor] = {
                    "shocked_by": sym,
                    "move_pct": move,
                    "warning": f"{sym} {direction} {sign}{move}% today — {sector} sector under pressure",
                    "reduce_size": True,
                    "scanned_at": time.time(),
                }

    return warnings


def load_shock_cache() -> dict:
    path = Path("data/shock_cache.json")
    if path.exists():
        try:
            data = json.loads(path.read_text())
            # Expire cache after 6 hours
            cutoff = time.time() - 6 * 3600
            return {k: v for k, v in data.items()
                    if v.get("scanned_at", 0) > cutoff}
        except Exception:
            return {}
    return {}


def save_shock_cache(warnings: dict):
    path = Path("data/shock_cache.json")
    path.write_text(json.dumps(warnings, indent=2))
