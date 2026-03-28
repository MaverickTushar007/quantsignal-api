"""
Persistent dedup using Supabase — survives Railway restarts.
Falls back to in-memory if Supabase unavailable.
"""
import time, os, logging
log = logging.getLogger(__name__)

COOLDOWN_HOURS = 6
_memory_fallback: dict[str, float] = {}

def _get_last_alerted(symbol: str) -> float:
    try:
        from supabase import create_client
        sb = create_client(os.environ["SUPABASE_URL"],
                           os.environ.get("SUPABASE_ANON_KEY") or os.environ["SUPABASE_KEY"])
        res = sb.table("alert_dedup").select("alerted_at").eq("symbol", symbol).execute()
        if res.data:
            from datetime import datetime, timezone
            ts = res.data[0]["alerted_at"]
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            return dt.timestamp()
    except Exception as e:
        log.debug(f"[dedup] supabase read failed: {e}")
    return _memory_fallback.get(symbol, 0)

def _set_alerted(symbol: str):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    _memory_fallback[symbol] = time.time()
    try:
        from supabase import create_client
        sb = create_client(os.environ["SUPABASE_URL"],
                           os.environ.get("SUPABASE_ANON_KEY") or os.environ["SUPABASE_KEY"])
        sb.table("alert_dedup").upsert({"symbol": symbol, "alerted_at": now},
                                        on_conflict="symbol").execute()
    except Exception as e:
        log.debug(f"[dedup] supabase write failed: {e}")

def should_alert(symbol: str) -> bool:
    last = _get_last_alerted(symbol)
    if time.time() - last > COOLDOWN_HOURS * 3600:
        _set_alerted(symbol)
        return True
    return False
