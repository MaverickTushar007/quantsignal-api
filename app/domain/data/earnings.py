from app.core.config import BASE_DIR
"""
data/earnings.py
Fetch and cache upcoming earnings dates for all stock tickers.
Flags signals where earnings are within 7 days.
"""
import json, time
import yfinance as yf
from datetime import date, datetime, timedelta
from pathlib import Path

EARNINGS_CACHE = BASE_DIR / "data/earnings_cache.json"
WARN_DAYS = 7  # flag if earnings within this many days

def fetch_earnings_dates(symbols: list) -> dict:
    """Fetch earnings dates for a list of symbols. Returns {symbol: date_str or None}"""
    results = {}
    for sym in symbols:
        try:
            cal = yf.Ticker(sym).calendar
            if cal and "Earnings Date" in cal and cal["Earnings Date"]:
                ed = cal["Earnings Date"][0]
                # ed is already a date object
                results[sym] = str(ed)
                print(f"✓ {sym}: earnings {ed}")
            else:
                results[sym] = None
        except Exception as e:
            results[sym] = None
        time.sleep(0.2)
    return results

def rebuild_earnings_cache(tickers: list) -> dict:
    """Rebuild full earnings cache for all stock tickers (skip crypto/forex/commodity)."""
    stock_types = {"STOCK", "IN_STOCK", "ETF", "INDEX"}
    symbols = [t["symbol"] for t in tickers if t.get("type") in stock_types]
    print(f"Fetching earnings dates for {len(symbols)} stocks...")
    results = fetch_earnings_dates(symbols)
    EARNINGS_CACHE.write_text(json.dumps({
        "updated": str(date.today()),
        "earnings": results
    }, indent=2))
    print(f"Earnings cache saved: {len([v for v in results.values() if v])} dates found")
    return results

def get_earnings_flag(symbol: str) -> dict | None:
    """
    Returns earnings warning dict if earnings within WARN_DAYS, else None.
    {days_until: int, date: str, warning: str}
    """
    try:
        if not EARNINGS_CACHE.exists():
            return None
        cache = json.loads(EARNINGS_CACHE.read_text())
        earnings = cache.get("earnings", {})
        date_str = earnings.get(symbol)
        if not date_str:
            return None
        earnings_date = date.fromisoformat(date_str)
        today = date.today()
        days_until = (earnings_date - today).days
        if 0 <= days_until <= WARN_DAYS:
            if days_until == 0:
                label = "TODAY"
            elif days_until == 1:
                label = "TOMORROW"
            else:
                label = f"IN {days_until} DAYS"
            return {
                "days_until": days_until,
                "date": date_str,
                "label": label,
                "warning": f"Earnings {label} — signal reliability reduced"
            }
        return None
    except Exception:
        return None
