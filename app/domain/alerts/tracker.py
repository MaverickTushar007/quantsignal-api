import os, logging
from datetime import datetime, timezone, timedelta

log = logging.getLogger(__name__)

def _sb():
    from supabase import create_client
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_ANON_KEY")
    return create_client(url, key)

def log_alert(sig: dict, channel: str):
    try:
        _sb().table("alert_events").insert({
            "symbol": sig.get("symbol"),
            "direction": sig.get("direction"),
            "probability": sig.get("probability", 0),
            "channel": channel,
            "entry_price": sig.get("current_price"),
            "outcome": None,
        }).execute()
        log.info(f"[tracker] logged {channel} alert for {sig.get('symbol')}")
    except Exception as e:
        log.warning(f"[tracker] log_alert failed: {e}")

def evaluate_outcomes():
    try:
        import yfinance as yf
        sb = _sb()
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        res = sb.table("alert_events").select("*").is_(
            "outcome", "null"
        ).lt("fired_at", cutoff).execute()
        rows = res.data or []
        if not rows:
            return 0
        evaluated = 0
        for row in rows:
            try:
                symbol = row["symbol"]
                direction = row["direction"]
                entry = row["entry_price"] or 0
                if not entry:
                    continue
                ticker = yf.Ticker(symbol)
                hist = ticker.history(period="1d")
                if hist.empty:
                    continue
                exit_price = float(hist["Close"].iloc[-1])
                if direction == "BUY":
                    pnl_pct = ((exit_price - entry) / entry) * 100
                elif direction == "SELL":
                    pnl_pct = ((entry - exit_price) / entry) * 100
                else:
                    continue
                outcome = "WIN" if pnl_pct > 0 else "LOSS"
                sb.table("alert_events").update({
                    "outcome": outcome,
                    "exit_price": exit_price,
                    "pnl_pct": round(pnl_pct, 4),
                    "evaluated_at": datetime.now(timezone.utc).isoformat(),
                }).eq("id", row["id"]).execute()
                evaluated += 1
            except Exception as e:
                log.warning(f"[tracker] eval failed for {row.get('symbol')}: {e}")
        return evaluated
    except Exception as e:
        log.warning(f"[tracker] evaluate_outcomes failed: {e}")
        return 0
