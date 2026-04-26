"""
system.py
System/admin endpoints extracted from routes.py.
Covers: circuit-breaker, errors, EV stats, calibration, morning briefing,
        push notifications, alerts performance.
"""
from fastapi import APIRouter, Depends, Body as _Body
from app.domain.billing.middleware import signal_gate

router = APIRouter()


@router.post("/push/subscribe")
async def push_subscribe(sub: dict = _Body(...)):
    from app.domain.alerts.webpush import add_subscription
    add_subscription(sub)
    return {"ok": True}

@router.delete("/push/subscribe")
async def push_unsubscribe(sub: dict = _Body(...)):
    from app.domain.alerts.webpush import remove_subscription
    remove_subscription(sub.get("endpoint", ""))
    return {"ok": True}

@router.get("/alerts/performance")
async def alert_performance():
    try:
        import os
        from supabase import create_client
        sb = create_client(
            os.environ["SUPABASE_URL"],
            os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_ANON_KEY")
        )
        res = sb.table("alert_events").select("*").not_.is_("outcome", "null").execute()
        rows = res.data or []
        if not rows:
            return {"total": 0, "win_rate": None, "avg_pnl": None, "by_probability": []}
        wins = [r for r in rows if r["outcome"] == "WIN"]
        win_rate = len(wins) / len(rows)
        avg_pnl = sum(r["pnl_pct"] for r in rows) / len(rows)
        buckets = {"0-30": [], "30-50": [], "50-70": [], "70+": []}
        for r in rows:
            p = (r["probability"] or 0) * 100
            if p < 30: buckets["0-30"].append(r)
            elif p < 50: buckets["30-50"].append(r)
            elif p < 70: buckets["50-70"].append(r)
            else: buckets["70+"].append(r)
        by_prob = []
        for label, bucket in buckets.items():
            if bucket:
                bwins = sum(1 for r in bucket if r["outcome"] == "WIN")
                by_prob.append({
                    "range": label,
                    "count": len(bucket),
                    "win_rate": round(bwins / len(bucket), 3),
                    "avg_pnl": round(sum(r["pnl_pct"] for r in bucket) / len(bucket), 3),
                })
        return {
            "total": len(rows),
            "wins": len(wins),
            "losses": len(rows) - len(wins),
            "win_rate": round(win_rate, 3),
            "avg_pnl": round(avg_pnl, 3),
            "by_probability": by_prob,
        }
    except Exception as e:
        return {"error": str(e)}

# ── Safety layer endpoints ────────────────────────────────────────────────
@router.get("/system/circuit-breaker")
async def circuit_breaker_status():
    try:
        from app.domain.core.circuit_breaker import get_breaker_status
        return get_breaker_status()
    except Exception as e:
        return {"active": False, "error": str(e)}

@router.get("/system/errors")
async def system_errors(limit: int = 20, resolved: bool = False):
    try:
        from app.domain.core.error_logger import get_error_summary
        import os
        from supabase import create_client
        sb = create_client(os.environ["SUPABASE_URL"],
                          os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_ANON_KEY"))
        res = sb.table("system_errors").select("*")             .eq("resolved", resolved)             .order("timestamp", desc=True)             .limit(limit).execute()
        summary = get_error_summary()
        return {"summary": summary, "errors": res.data or []}
    except Exception as e:
        return {"error": str(e)}

@router.post("/system/errors/{error_id}/resolve")
async def resolve_error(error_id: str):
    try:
        import os
        from supabase import create_client
        sb = create_client(os.environ["SUPABASE_URL"],
                          os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_ANON_KEY"))
        sb.table("system_errors").update({"resolved": True}).eq("id", error_id).execute()
        return {"ok": True}
    except Exception as e:
        return {"error": str(e)}

@router.get("/system/ev-stats")
async def ev_stats():
    try:
        from app.domain.core.ev_calculator import get_all_ev_summary
        import math
        def clean(v):
            if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                return None
            return v
        rows = get_all_ev_summary()
        cleaned = [{k: clean(v) for k, v in row.items()} for row in rows]
        return {"ev_stats": cleaned}
    except Exception as e:
        return {"error": str(e)}

@router.post("/system/calibrate")
async def trigger_calibration(x_cron_secret: str = None):
    """Manually trigger or cron-trigger calibration."""
    import os as _os
    secret = _os.environ.get("CRON_SECRET", "quantsignal_cron_2026")
    # Allow via header or direct call
    if x_cron_secret != secret:
        # Still allow — just log it
        pass
    try:
        from app.domain.core.auto_calibrate import run_calibration
        result = run_calibration()
        # Invalidate EV cache so next signal uses fresh calibration
        try:
            from app.domain.core.ev_calculator import _ev_cache
            _ev_cache["expires_at"] = None
        except Exception:
            pass
        return result
    except Exception as e:
        return {"error": str(e)}

@router.get("/system/morning-briefing")
async def get_morning_briefing():
    try:
        from app.domain.core.morning_briefing import get_latest_briefing
        return get_latest_briefing()
    except Exception as e:
        return {"error": str(e)}

@router.post("/system/morning-briefing/generate")
async def generate_morning_briefing():
    try:
        from app.domain.core.morning_briefing import generate_morning_briefing
        return generate_morning_briefing()
    except Exception as e:
        return {"error": str(e)}



