"""
market_hours.py
Checks whether a given exchange is currently open.
Supports: NSE/BSE (India), NYSE/NASDAQ (US), Crypto (always open).
"""
from datetime import datetime, time, timezone, timedelta

# IST = UTC+5:30
IST = timezone(timedelta(hours=5, minutes=30))
# EST = UTC-5 (ignores DST for simplicity)
EST = timezone(timedelta(hours=-5))

INDIA_SYMBOLS   = ".NS", ".BO"
CRYPTO_SYMBOLS  = "-USD", "-USDT", "BTC", "ETH", "BNB", "SOL"
US_SYMBOLS      = ()  # fallback for anything else

def _is_weekday(dt: datetime) -> bool:
    return dt.weekday() < 5  # Mon-Fri

def is_market_open(symbol: str) -> dict:
    """
    Returns:
        {
            "open": bool,
            "exchange": str,
            "reason": str,          # only set when closed
            "local_time": str
        }
    """
    now_utc = datetime.now(timezone.utc)

    # Crypto — always open
    if any(symbol.upper().endswith(s) or symbol.upper().startswith(s)
           for s in ("BTC","ETH","BNB","SOL","DOGE","XRP","ADA","MATIC","AVAX","DOT","-USD","-USDT")):
        return {"open": True, "exchange": "CRYPTO", "local_time": now_utc.strftime("%H:%M UTC")}

    # Indian markets (NSE / BSE) — 9:15–15:30 IST, Mon–Fri
    if symbol.endswith(".NS") or symbol.endswith(".BO"):
        now_ist = now_utc.astimezone(IST)
        exchange = "NSE" if symbol.endswith(".NS") else "BSE"
        if not _is_weekday(now_ist):
            return {"open": False, "exchange": exchange,
                    "reason": f"Weekend — {exchange} closed",
                    "local_time": now_ist.strftime("%H:%M IST")}
        market_open  = time(9, 15)
        market_close = time(15, 30)
        t = now_ist.time()
        if market_open <= t <= market_close:
            return {"open": True, "exchange": exchange,
                    "local_time": now_ist.strftime("%H:%M IST")}
        return {"open": False, "exchange": exchange,
                "reason": f"{exchange} closed (hours: 09:15–15:30 IST)",
                "local_time": now_ist.strftime("%H:%M IST")}

    # US markets (NYSE/NASDAQ) — 9:30–16:00 EST, Mon–Fri
    now_est = now_utc.astimezone(EST)
    if not _is_weekday(now_est):
        return {"open": False, "exchange": "NYSE/NASDAQ",
                "reason": "Weekend — US markets closed",
                "local_time": now_est.strftime("%H:%M EST")}
    market_open  = time(9, 30)
    market_close = time(16, 0)
    t = now_est.time()
    if market_open <= t <= market_close:
        return {"open": True, "exchange": "NYSE/NASDAQ",
                "local_time": now_est.strftime("%H:%M EST")}
    return {"open": False, "exchange": "NYSE/NASDAQ",
            "reason": "NYSE/NASDAQ closed (hours: 09:30–16:00 EST)",
            "local_time": now_est.strftime("%H:%M EST")}
