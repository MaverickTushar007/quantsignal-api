"""
api/routes/admin.py
Admin dashboard — system health, error patterns, signal quality metrics.
GET /admin/dashboard   → full system overview
GET /admin/signals     → signal quality stats by symbol
GET /admin/errors      → recent errors from Railway logs
"""
import logging
import os
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Header
from typing import Optional

router = APIRouter()
log = logging.getLogger(__name__)


def _sb():
    from supabase import create_client
    return create_client(
        os.environ.get("SUPABASE_URL", ""),
        os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_ANON_KEY", "")
    )


@router.get("/admin/dashboard")
def admin_dashboard():
    """Full system health overview."""
    now      = datetime.now(timezone.utc)
    day_ago  = now - timedelta(hours=24)
    week_ago = now - timedelta(days=7)

    result = {
        "generated_at": now.isoformat(),
        "signal_quality": {},
        "context_coverage": {},
        "weekly_volume": {},
        "top_symbols": [],
        "system_health": "ok",
    }

    try:
        sb = _sb()

        # Signal volume last 24h and 7d
        day_res  = sb.table("signal_context").select("id", count="exact") \
            .gte("generated_at", day_ago.isoformat()).execute()
        week_res = sb.table("signal_context").select("id", count="exact") \
            .gte("generated_at", week_ago.isoformat()).execute()

        result["weekly_volume"] = {
            "last_24h": day_res.count or 0,
            "last_7d":  week_res.count or 0,
        }

        # Context coverage — how many signals have EV scores
        ev_res = sb.table("signal_context").select("id", count="exact") \
            .gte("generated_at", week_ago.isoformat()) \
            .not_.is_("ev_score", "null").execute()

        total = week_res.count or 1
        with_ev = ev_res.count or 0
        result["context_coverage"] = {
            "total_signals":   total,
            "with_ev_score":   with_ev,
            "ev_coverage_pct": f"{with_ev/total:.0%}",
        }

        # Top symbols by signal count
        sym_res = sb.table("signal_context") \
            .select("symbol,direction,ev_score") \
            .gte("generated_at", week_ago.isoformat()) \
            .limit(200).execute()

        from collections import Counter
        sym_counts = Counter(r["symbol"] for r in (sym_res.data or []))
        result["top_symbols"] = [
            {"symbol": s, "count": c}
            for s, c in sym_counts.most_common(10)
        ]

        # Signal quality — avg EV by direction
        rows = sym_res.data or []
        buy_evs  = [r["ev_score"] for r in rows if r.get("ev_score") and r.get("direction") == "BUY"]
        sell_evs = [r["ev_score"] for r in rows if r.get("ev_score") and r.get("direction") == "SELL"]
        result["signal_quality"] = {
            "avg_ev_buy":  f"{sum(buy_evs)/len(buy_evs):+.2f}%" if buy_evs else "N/A",
            "avg_ev_sell": f"{sum(sell_evs)/len(sell_evs):+.2f}%" if sell_evs else "N/A",
            "buy_count":   len(buy_evs),
            "sell_count":  len(sell_evs),
        }

    except Exception as e:
        log.warning(f"[admin] dashboard query failed: {e}")
        result["system_health"] = f"degraded: {e}"

    return result


@router.get("/admin/signals")
def admin_signals(symbol: Optional[str] = None):
    """Signal quality breakdown per symbol."""
    try:
        sb  = _sb()
        q   = sb.table("signal_context").select("symbol,direction,ev_score,energy_state,conflict_detected")
        if symbol:
            q = q.eq("symbol", symbol.upper())
        res  = q.limit(500).execute()
        rows = res.data or []

        from collections import defaultdict
        stats = defaultdict(lambda: {"total": 0, "with_ev": 0, "ev_sum": 0, "conflicts": 0, "directions": {}})
        for r in rows:
            s = r["symbol"]
            stats[s]["total"] += 1
            if r.get("ev_score"):
                stats[s]["with_ev"]  += 1
                stats[s]["ev_sum"]   += r["ev_score"]
            if r.get("conflict_detected"):
                stats[s]["conflicts"] += 1
            d = r.get("direction", "HOLD")
            stats[s]["directions"][d] = stats[s]["directions"].get(d, 0) + 1

        output = []
        for sym, st in sorted(stats.items()):
            avg_ev = st["ev_sum"] / st["with_ev"] if st["with_ev"] else None
            output.append({
                "symbol":     sym,
                "total":      st["total"],
                "avg_ev":     f"{avg_ev:+.2f}%" if avg_ev is not None else "N/A",
                "conflicts":  st["conflicts"],
                "directions": st["directions"],
            })
        return {"symbols": output, "total_symbols": len(output)}
    except Exception as e:
        return {"error": str(e)}


@router.get("/admin/weekly-reports")
def admin_weekly_reports():
    """List all generated weekly reports."""
    try:
        sb  = _sb()
        res = sb.table("weekly_reports").select("user_id,generated_at,report") \
            .order("generated_at", desc=True).limit(20).execute()
        return {"reports": res.data or [], "count": len(res.data or [])}
    except Exception as e:
        return {"error": str(e)}


@router.post("/admin/cache/wipe")
async def wipe_signal_cache():
    """Nuclear cache clear — wipes JSON file + all Redis signal keys."""
    import json
    from app.core.config import BASE_DIR
    wiped = {"json_file": False, "redis_keys": 0}
    try:
        cache_path = BASE_DIR / "data/signals_cache.json"
        cache_path.write_text("{}")
        wiped["json_file"] = True
    except Exception as e:
        wiped["json_error"] = str(e)
    try:
        from app.infrastructure.cache.cache import _get_redis
        r = _get_redis()
        if r:
            keys = r.keys("signal:*")
            if keys:
                r.delete(*keys)
                wiped["redis_keys"] = len(keys)
    except Exception as e:
        wiped["redis_error"] = str(e)
    return {"status": "wiped", "detail": wiped}

@router.post("/admin/expire-signal/{signal_id}", tags=["admin"])
def expire_signal(signal_id: int):
    """One-time use: manually expire a bad signal by ID."""
    from app.infrastructure.db.signal_history import update_outcome, get_open_signals
    signals = get_open_signals()
    match = next((s for s in signals if s["id"] == signal_id), None)
    if not match:
        raise HTTPException(status_code=404, detail=f"Open signal {signal_id} not found")
    update_outcome(signal_id, "expired", match["entry_price"])
    return {"expired": signal_id, "symbol": match["symbol"]}
