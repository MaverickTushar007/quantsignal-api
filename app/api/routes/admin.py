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
from fastapi import Request, APIRouter, Header
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

@router.post("/admin/fix-null-outcomes")
async def fix_null_outcomes():
    from app.infrastructure.db.signal_history import _get_conn
    con, db = _get_conn()
    try:
        cur = con.cursor()
        cur.execute(
            "UPDATE signal_history SET outcome = 'open' WHERE outcome IS NULL AND direction IN ('BUY','SELL')"
            if db == "pg" else
            "UPDATE signal_history SET outcome = 'open' WHERE outcome IS NULL AND direction IN ('BUY','SELL')"
        )
        updated = cur.rowcount
        con.commit()
        return {"updated": updated, "message": f"Fixed {updated} signals with NULL outcome"}
    finally:
        con.close()

@router.get("/admin/signals/raw")
async def raw_signals():
    from app.infrastructure.db.signal_history import _get_conn
    con, db = _get_conn()
    try:
        cur = con.cursor()
        cur.execute("SELECT id, symbol, direction, outcome, entry_price, take_profit, stop_loss, generated_at, evaluated_at FROM signal_history ORDER BY id DESC")
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, row)) for row in cur.fetchall()]
        return {"total": len(rows), "signals": rows}
    finally:
        con.close()

@router.post("/admin/watcher/trigger")
async def trigger_watcher():
    from app.infrastructure.scheduler.perseus_watcher import scan_and_alert
    import threading
    t = threading.Thread(target=scan_and_alert, daemon=True)
    t.start()
    return {"status": "watcher triggered — check signals in 3 minutes"}

@router.post("/admin/models/wipe")
async def wipe_models():
    """Delete all cached ML model pickles — forces retrain on next signal request."""
    import glob
    from app.core.config import BASE_DIR
    files = glob.glob(str(BASE_DIR / "data/models/*.pkl"))
    for f in files:
        try:
            import os; os.remove(f)
        except Exception:
            pass
    return {"wiped": len(files), "message": f"Deleted {len(files)} model pickles — will retrain on next request"}

@router.post("/admin/db/cleanup", tags=["admin"])
def cleanup_bad_signals():
    """Expire pre-tier junk signals from Railway Postgres."""
    from app.infrastructure.db.signal_history import _get_conn
    con, db = _get_conn()
    try:
        cur = con.cursor()
        if db == "pg":
            cur.execute("""
                UPDATE signal_history
                SET outcome = 'expired', evaluated_at = NOW()
                WHERE outcome = 'open'
                AND (
                    confluence_score < 4
                    OR generated_at < NOW() - INTERVAL '7 days'
                )
            """)
        else:
            cur.execute("""
                UPDATE signal_history
                SET outcome = 'expired', evaluated_at = datetime('now')
                WHERE outcome = 'open'
                AND (
                    confluence_score < 4
                    OR generated_at < datetime('now', '-7 days')
                )
            """)
        count = cur.rowcount
        con.commit()
        return {"status": "ok", "expired": count}
    finally:
        con.close()


@router.get("/admin/job-health", tags=["admin"])
def job_health():
    """Return consecutive failure counts for all tracked jobs."""
    from app.domain.core.failure_tracker import get_all_status
    status = get_all_status()
    unhealthy = {k: v for k, v in status.items() if not v["healthy"]}
    return {
        "healthy": len(unhealthy) == 0,
        "unhealthy_jobs": unhealthy,
        "all_jobs": status,
    }


@router.get("/admin/circuit-breaker", tags=["admin"])
def circuit_breaker_status():
    """Return current circuit breaker state."""
    try:
        from app.domain.core.circuit_breaker_v2 import CircuitBreaker
        return CircuitBreaker.get_status()
    except Exception as e:
        return {"error": str(e)}

@router.post("/admin/circuit-breaker/reset", tags=["admin"])
def circuit_breaker_reset():
    """Manually reset the circuit breaker."""
    try:
        from app.domain.core.circuit_breaker_v2 import CircuitBreaker
        CircuitBreaker.reset()
        return {"status": "reset", "state": CircuitBreaker.get_status()}
    except Exception as e:
        return {"error": str(e)}


@router.post("/admin/cleanup-duplicate-signals", tags=["admin"])
def cleanup_duplicate_signals(x_admin_key: str = Header(None, alias="x-admin-key")):
    """Delete duplicate signals — keep only latest per symbol per day."""
    if x_admin_key != "quantsignal-admin-2026":
        from fastapi import Request, HTTPException
        raise HTTPException(status_code=401, detail="Unauthorized")
    try:
        from app.infrastructure.db.signal_history import _get_conn
        con, db = _get_conn()
        cur = con.cursor()
        cur.execute("SELECT COUNT(*) FROM signal_history")
        before = cur.fetchone()[0]
        if db == "pg":
            cur.execute("""
                DELETE FROM signal_history
                WHERE id NOT IN (
                    SELECT MAX(id)
                    FROM signal_history
                    GROUP BY symbol, DATE(generated_at)
                )
            """)
        else:
            cur.execute("""
                DELETE FROM signal_history
                WHERE id NOT IN (
                    SELECT MAX(id)
                    FROM signal_history
                    GROUP BY symbol, DATE(generated_at)
                )
            """)
        deleted = cur.rowcount
        con.commit()
        cur.execute("SELECT COUNT(*) FROM signal_history")
        after = cur.fetchone()[0]
        cur.execute("SELECT outcome, COUNT(*) FROM signal_history GROUP BY outcome")
        breakdown = {r[0]: r[1] for r in cur.fetchall()}
        con.close()
        return {"before": before, "after": after, "deleted": deleted, "breakdown": breakdown}
    except Exception as e:
        return {"error": str(e)}


@router.get("/admin/rate-limit/{user_id}", tags=["admin"])
def get_user_quota(user_id: str):
    """Inspect a user's current daily signal quota."""
    from app.domain.core.rate_limiter import get_usage
    return {"user_id": user_id, **get_usage(user_id)}

@router.post("/admin/rate-limit/{user_id}/reset", tags=["admin"])
def reset_user_quota(user_id: str, x_admin_key: str = Header(None, alias="x-admin-key")):
    """Reset a user's daily quota (admin only)."""
    if x_admin_key != "quantsignal-admin-2026":
        from fastapi import Request, HTTPException
        raise HTTPException(status_code=401, detail="Unauthorized")
    from app.domain.core.rate_limiter import reset_user
    ok = reset_user(user_id)
    return {"user_id": user_id, "reset": ok}


@router.get("/admin/auth-debug", tags=["admin"])
async def auth_debug(request: Request):
    """Debug JWT decode — shows what token resolves to."""
    from fastapi import Request
    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        return {"error": "no bearer token"}
    token = auth_header[7:]
    
    import urllib.request, json
    supabase_url = "https://xvwkloqmzgwqsouxhgiy.supabase.co"
    jwks_url = f"{supabase_url}/auth/v1/.well-known/jwks.json"
    
    try:
        with urllib.request.urlopen(jwks_url, timeout=5) as r:
            jwks = json.loads(r.read())
        keys = jwks.get("keys", [])
        key_types = [k.get("kty") for k in keys]
        
        import jwt
        header = jwt.get_unverified_header(token)
        
        return {
            "jwks_reachable": True,
            "key_count": len(keys),
            "key_types": key_types,
            "token_header": header,
            "token_preview": token[:20] + "...",
        }
    except Exception as e:
        return {"jwks_reachable": False, "error": str(e)}
