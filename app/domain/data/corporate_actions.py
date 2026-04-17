"""
corporate_actions.py
Checks for recent splits, dividends, or bonus issues via yfinance.
Flags signals where historical OHLCV may be unreliable due to recent actions.
"""
import logging
from datetime import datetime, timezone, timedelta

log = logging.getLogger(__name__)

def get_recent_corporate_actions(symbol: str, lookback_days: int = 30) -> dict:
    """
    Returns:
        {
            "has_action": bool,
            "actions": list of {"type": str, "date": str, "value": float},
            "warning": str or None
        }
    Never raises.
    """
    result = {"has_action": False, "actions": [], "warning": None}
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)

        actions = []

        # Check splits
        try:
            splits = ticker.splits
            if splits is not None and len(splits) > 0:
                for dt, val in splits.items():
                    dt_utc = dt.to_pydatetime()
                    if dt_utc.tzinfo is None:
                        dt_utc = dt_utc.replace(tzinfo=timezone.utc)
                    if dt_utc >= cutoff and val != 1.0:
                        actions.append({
                            "type": "split",
                            "date": dt_utc.strftime("%Y-%m-%d"),
                            "value": round(float(val), 4)
                        })
        except Exception:
            pass

        # Check dividends
        try:
            divs = ticker.dividends
            if divs is not None and len(divs) > 0:
                for dt, val in divs.items():
                    dt_utc = dt.to_pydatetime()
                    if dt_utc.tzinfo is None:
                        dt_utc = dt_utc.replace(tzinfo=timezone.utc)
                    if dt_utc >= cutoff and val > 0:
                        actions.append({
                            "type": "dividend",
                            "date": dt_utc.strftime("%Y-%m-%d"),
                            "value": round(float(val), 4)
                        })
        except Exception:
            pass

        if actions:
            result["has_action"] = True
            result["actions"] = actions
            types = list({a["type"] for a in actions})
            result["warning"] = (
                f"Recent corporate action ({', '.join(types)}) in last {lookback_days}d — "
                f"historical OHLCV may be adjusted. Verify signal manually."
            )
            log.warning(f"[{symbol}] Corporate actions detected: {actions}")

    except Exception as e:
        log.debug(f"[corporate_actions] {symbol} check failed: {e}")

    return result
