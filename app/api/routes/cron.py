"""
api/routes/cron.py
Cron HTTP endpoints — thin handlers that delegate to tasks._rebuild.
"""
from fastapi import APIRouter, Header, HTTPException, Request
from app.api.routes.tasks import _rebuild, CRON_SECRET
import os, json, time, threading
from pathlib import Path

router = APIRouter()

@router.post("/cron/refresh", tags=["cron"])
def refresh_cache(x_cron_secret: str = Header(None, alias="X-Cron-Secret")):
    if x_cron_secret != CRON_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")

    def _rebuild_and_calibrate():
        _rebuild()
        # Run auto-calibration after every cache rebuild
        try:
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc)
            # Only run calibration on first rebuild of the day (hour 0-1 UTC)
            # OR always run if triggered manually — check env flag
            import os
            always_calibrate = os.environ.get("ALWAYS_CALIBRATE", "false").lower() == "true"
            if always_calibrate or now.hour in (0, 1, 2):
                from app.domain.core.auto_calibrate import run_calibration
                result = run_calibration()
                print(f"[cron] auto-calibration: {result}")
                # Invalidate EV cache
                try:
                    from app.domain.core.ev_calculator import _ev_cache
                    _ev_cache["expires_at"] = None
                except Exception:
                    pass
        except Exception as e:
            print(f"[cron] calibration skipped: {e}")

    thread = threading.Thread(target=_rebuild_and_calibrate, daemon=True)
    thread.start()
    return {"status": "started", "message": "Cache rebuild running in background"}

@router.post("/cron/retrain", tags=["cron"])
def trigger_retrain(x_cron_secret: str = Header(None, alias="X-Cron-Secret")):
    """Manually trigger auto-retrain of weak models."""
    if x_cron_secret != CRON_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    def _run():
        try:
            cache = json.loads((BASE_DIR / "data/signals_cache.json").read_text())
            from app.domain.ml.auto_retrain import run_auto_retrain
            summary = run_auto_retrain(list(cache.keys()))
            print(f"Manual retrain complete: {summary}")
        except Exception as e:
            print(f"Manual retrain failed: {e}")
    
    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return {"status": "started", "message": "Auto-retrain running in background"}

@router.get("/cron/status", tags=["cron"])
def cache_status():
    try:
        cache_path = BASE_DIR / "data/signals_cache.json"
        cache = json.loads(cache_path.read_text())
        from app.domain.data.universe import TICKERS
        import os
        mtime = os.path.getmtime(cache_path)
        from datetime import datetime, timezone
        last_rebuilt = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
        # Count by type
        types = {}
        for sig in cache.values():
            t = sig.get("type", "UNKNOWN")
            types[t] = types.get(t, 0) + 1
        return {
            "cached": len(cache),
            "total": len(TICKERS),
            "coverage": f"{len(cache)}/{len(TICKERS)}",
            "last_rebuilt": last_rebuilt,
            "by_type": types,
            "btc_price": cache.get("BTC-USD", {}).get("current_price"),
            "btc_signal": cache.get("BTC-USD", {}).get("direction"),
            "nifty_signal": cache.get("^NSEI", {}).get("direction"),
        }
    except Exception as e:
        return {"error": str(e)}

@router.post("/cron/rebuild-mtf", tags=["cron"])
def rebuild_mtf_cache():
    """One-shot: attach MTF to all cached signals + flush Redis."""
    import json, time
    from pathlib import Path
    from app.domain.data.mtf import fetch_mtf_features

    cache = json.loads((BASE_DIR / "data/signals_cache.json").read_text())
    updated = 0
    for sym, sig in cache.items():
        try:
            mtf = fetch_mtf_features(sym)
            daily_bull = sig.get("direction") == "BUY"
            mtf["mtf_score_with_daily"] = mtf["mtf_score"] + (1 if daily_bull else 0)
            mtf["mtf_details"]["1d"] = "BULL" if daily_bull else "BEAR"
            sig["mtf"] = mtf
            updated += 1
            # Also update Redis cache directly
            try:
                from app.infrastructure.cache.cache import set_cached
                set_cached(f"signal:{sym}", sig, ttl=3600)
            except Exception:
                pass
        except Exception:
            pass
        time.sleep(0.2)
    (BASE_DIR / "data/signals_cache.json").write_text(json.dumps(cache, indent=2))
    try:
        from app.infrastructure.cache.cache import set_cached
        set_cached("signals_cache_full", cache, ttl=86400)
    except Exception:
        pass
    return {"updated": updated, "total": len(cache)}

@router.post("/cron/flush-signal-cache", tags=["cron"])
def flush_signal_cache():
    """Delete all signal:* keys from Redis AND clear JSON cache so fresh signals regenerate."""
    result = {"redis_flushed": 0, "json_cache_cleared": False}
    try:
        from app.infrastructure.cache.cache import _get_redis
        r = _get_redis()
        if r:
            keys = r.keys("signal:*")
            if keys:
                for key in keys:
                    r.delete(key)
            result["redis_flushed"] = len(keys) if keys else 0
    except Exception as e:
        result["redis_error"] = str(e)
    try:
        import json
        cache_path = BASE_DIR / "data/signals_cache.json"
        if cache_path.exists():
            cache_path.write_text("{}")
            result["json_cache_cleared"] = True
    except Exception as e:
        result["json_error"] = str(e)
    return result


@router.post("/cron/check-outcomes", tags=["cron"])
def check_outcomes(x_cron_secret: str = Header(None)):
    if x_cron_secret != CRON_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")
    def _run():
        try:
            from app.domain.core.circuit_breaker_v2 import evaluate_and_update_outcomes
            from app.domain.performance.evaluator import evaluate_open_signals
            result = evaluate_open_signals()
            print(f"Outcome check complete: {result}")
        except Exception as e:
            print(f"Outcome check error: {e}")
    threading.Thread(target=_run, daemon=True).start()
    return {"message": "Outcome check running in background"}

@router.post("/cron/evaluate-alerts", tags=["cron"])
def evaluate_alerts(x_cron_secret: str = Header(None, alias="X-Cron-Secret")):
    if x_cron_secret != CRON_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")
    try:
        from app.domain.alerts.tracker import evaluate_outcomes
        evaluated = evaluate_outcomes()
    
        # Model degradation check (runs daily)
        try:
            from app.domain.core.degradation_detector import check_all
            deg_results = check_all()
            degraded = [s for s, r in deg_results.items() if r.get("degraded")]
            if degraded:
                print(f"[Degradation] WARNING: {degraded}")
            else:
                print(f"[Degradation] All {len(deg_results)} symbols healthy")
        except Exception as _e:
            print(f"Degradation check error: {_e}")

        # Walk-forward validation (runs on every 7th cron cycle ~weekly)
        try:
            import time as _time
            _wfv_flag = "data/wfv_last_run.txt"
            _run_wfv = True
            try:
                _last = float(open(_wfv_flag).read().strip())
                _run_wfv = (_time.time() - _last) > 7 * 86400
            except Exception:
                pass
            if _run_wfv:
                from app.domain.ml.walk_forward import validate_all
                _wfv_symbols = ["BTC-USD", "ETH-USD", "SOL-USD", "TSLA", "RELIANCE.NS"]
                _wfv_results = validate_all(_wfv_symbols)
                _overfitted = [s for s, r in _wfv_results.items() if r.is_overfitted]
                if _overfitted:
                    print(f"[WFV] WARNING overfitted symbols: {_overfitted}")
                else:
                    print(f"[WFV] All symbols passed walk-forward validation")
                open(_wfv_flag, "w").write(str(_time.time()))
        except Exception as _e:
            print(f"WFV error: {_e}")

        # W4.6 — Weekly Sharpe tracking
        try:
            if _run_wfv:
                from app.domain.core.sharpe_tracker import run_weekly_sharpe
                _sharpe_result = run_weekly_sharpe()
                _alerts = _sharpe_result.get("alerts", [])
                if _alerts:
                    print(f"[SharpeTracker] alerts: {_alerts}")
                else:
                    print(f"[SharpeTracker] {_sharpe_result.get('computed', 0)} classes tracked")
        except Exception as _ste:
            print(f"[SharpeTracker] error: {_ste}")

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"status": "ok", "evaluated": evaluated}

@router.post("/cron/upload-cache", tags=["cron"])
async def upload_cache(request: Request, x_cron_secret: str = Header(None, alias="X-Cron-Secret")):
    """Receive a pre-built signals cache from local machine and write to disk."""
    if x_cron_secret != CRON_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")
    try:
        body = await request.json()
        if not isinstance(body, dict) or len(body) == 0:
            return {"error": "empty or invalid cache"}
        cache_path = BASE_DIR / "data/signals_cache.json"
        import json as _json
        cache_path.write_text(_json.dumps(body, indent=2))
        try:
            from app.infrastructure.cache.cache import _get_redis
            r = _get_redis()
            if r:
                keys = r.keys("signal:*")
                for k in keys:
                    r.delete(k)
        except Exception:
            pass
        return {"status": "ok", "signals_written": len(body)}
    except Exception as e:
        return {"error": str(e)}

@router.post("/cron/guardian", tags=["cron"])
def guardian_cron(x_cron_secret: str = Header(None, alias="X-Cron-Secret")):
    """15-minute Guardian monitoring cycle — called by Railway cron."""
    if x_cron_secret != CRON_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")
    def _run():
        try:
            from app.domain.agents.guardian_agent import run as guardian_run
            result = guardian_run(user_id="default")
            print(f"[guardian cron] watched={len(result.get('watched',[]))}, alerts={len(result.get('alerts_fired',[]))}")
        except Exception as e:
            print(f"[guardian cron] failed: {e}")
    import threading
    threading.Thread(target=_run, daemon=True).start()
    return {"status": "started"}


@router.post("/cron/weekly-sharpe", tags=["cron"])
def trigger_weekly_sharpe(x_cron_secret: str = Header(None, alias="X-Cron-Secret")):
    if x_cron_secret != CRON_SECRET:
        raise HTTPException(status_code=401, detail="Invalid cron secret")
    try:
        from app.domain.core.sharpe_tracker import run_weekly_sharpe
        result = run_weekly_sharpe()
        return {"status": "ok", **result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
