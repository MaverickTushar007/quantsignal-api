from app.core.config import BASE_DIR
"""
api/cron.py
Cache refresh endpoint — called by Railway cron or external scheduler.
Rebuilds all 118 signals and clears Redis cache.
"""
from fastapi import APIRouter, Header, HTTPException, Request
import os, json, time, threading
from pathlib import Path

router = APIRouter()
CRON_SECRET = os.getenv("CRON_SECRET", "quantsignal-cron-2026")

def _rebuild():
    from app.domain.core.failure_tracker import record_failure, record_success
    from app.api.routes.alerts import fire_signal_alerts
    import json, time, threading
    from pathlib import Path
    from concurrent.futures import ThreadPoolExecutor, as_completed

    # Load old cache for alert comparison
    try:
        old_cache = json.loads((BASE_DIR / "data/signals_cache.json").read_text())
    except Exception:
        old_cache = {}

    try:
        from app.domain.data.universe import TICKERS
        from app.domain.signal.service import generate_signal

        # Split into 4 parallel worker groups
        GROUPS = {
            "CRYPTO":  [t for t in TICKERS if t["type"] == "CRYPTO"],
            "INDIA":   [t for t in TICKERS if t["type"] == "IN_STOCK"],
            "US":      [t for t in TICKERS if t["type"] in ("STOCK", "ETF")],
            "MACRO":   [t for t in TICKERS if t["type"] in ("INDEX", "FOREX", "COMMODITY")],
        }

        # Load existing cache as fallback — never serve empty dashboard
        try:
            existing_cache = json.loads((BASE_DIR / "data/signals_cache.json").read_text())
        except Exception:
            existing_cache = {}

        cache = {}
        cache_lock = threading.Lock()
        start_time = time.time()

        def process_group(group_name, tickers):
            results = {}
            for t in tickers:
                sym = t["symbol"]
                try:
                    sig = generate_signal(sym, include_reasoning=False)
                    if sig:
                        results[sym] = sig
                        record_success(f"signal:{sym}")
                        print(f"[{group_name}] ✓ {sym}: {sig['direction']}")
                    else:
                        record_failure(f"signal:{sym}")
                        print(f"[{group_name}] ✗ {sym}: no data")
                except Exception as e:
                    record_failure(f"signal:{sym}")
                    print(f"[{group_name}] ✗ {sym}: {e}")
                time.sleep(0.2)
            return group_name, results

        # Run all 4 groups in parallel
        print(f"Starting parallel rebuild — 4 workers for {len(TICKERS)} signals...")
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {
                executor.submit(process_group, name, tickers): name
                for name, tickers in GROUPS.items()
            }
            for future in as_completed(futures):
                group_name, results = future.result()
                with cache_lock:
                    cache.update(results)
                print(f"[{group_name}] done — {len(results)} signals")

        elapsed = round(time.time() - start_time, 1)
        # Never serve empty dashboard — merge with stale cache if rebuild produced fewer signals
        if len(cache) < len(existing_cache) * 0.5:
            print(f"[cron] WARNING: only {len(cache)} new signals vs {len(existing_cache)} before — merging with stale cache")
            cache = {**existing_cache, **cache}
        (BASE_DIR / "data/signals_cache.json").write_text(json.dumps(cache, indent=2))
        try:
            from app.infrastructure.cache.cache import set_cached
            set_cached("signals_cache_full", cache, ttl=86400)
            print("[cron] signals_cache_full written to Redis")
        except Exception as e:
            print(f"[cron] Redis write failed: {e}")
        print(f"Cache rebuilt: {len(cache)}/{len(TICKERS)} signals in {elapsed}s")

        # Run virtual agent executor
        try:
            from app.api.routes.agent_executor import run_agent_executor
            run_agent_executor()
        except Exception as e:
            print(f"Agent executor error: {e}")
        try:
            from app.api.routes.agent_executor import _close_hit_positions
            _close_hit_positions()
        except Exception as e:
            print(f"Outcome checker error: {e}")

        # Scan for cross-asset shocks
        try:
            from app.domain.data.correlations import scan_for_shocks, save_shock_cache
            shock_warnings = scan_for_shocks({}, threshold_pct=3.0)
            save_shock_cache(shock_warnings)
            print(f"Shock scan: {len(shock_warnings)} assets flagged")
        except Exception as e:
            print(f"Shock scan error: {e}")

        # Rebuild MTF cache daily
        try:
            rebuild_mtf_cache()
            print("MTF cache rebuilt")
        except Exception as e:
            print(f"MTF rebuild error: {e}")

        # Rebuild earnings cache daily
        try:
            from app.domain.data.earnings import rebuild_earnings_cache
            from app.domain.data.universe import TICKERS
            rebuild_earnings_cache(TICKERS)
            print("Earnings cache rebuilt")
        except Exception as e:
            print(f"Earnings cache error: {e}")

        # Auto-retrain weak models (Karpathy: verifiable metric → auto-improve)
        try:
            from app.domain.ml.auto_retrain import run_auto_retrain
            symbols = list(cache.keys())
            retrain_summary = run_auto_retrain(symbols)
            print(f"Auto-retrain: {retrain_summary['retrained']} models improved")
        except Exception as e:
            print(f"Auto-retrain error: {e}")

        # Evaluate open signals first (close wins/losses before saving new ones)
        try:
            from app.domain.core.circuit_breaker_v2 import evaluate_and_update_outcomes
            from app.domain.performance.evaluator import evaluate_open_signals
            eval_result = evaluate_open_signals()
            print(f"Signal evaluation: {eval_result}")
        except Exception as e:
            print(f"Signal evaluation error: {e}")

        # Detect failure patterns and log to system_errors
        try:
            from app.domain.core.error_logger import detect_signal_patterns
            pattern_result = detect_signal_patterns()
            print(f"Pattern detection: {pattern_result}")
        except Exception as e:
            print(f"Pattern detection error: {e}")

        # Save signals to history DB (feeds morning briefing + performance tracking)
        try:
            from app.infrastructure.db.signal_history import init_db, save_signal, is_open
            init_db()
            saved = 0
            for sym, sig in cache.items():
                if sig.get("direction") in ("BUY", "SELL"):
                    if not is_open(sym):
                        save_signal({**sig, "symbol": sym})
                        saved += 1
            print(f"Signal history: {saved} new signals recorded")
        except Exception as e:
            print(f"Signal history error: {e}")

        # Fire signal alerts for direction changes
        try:
            fired = fire_signal_alerts(cache, old_cache)
            print(f"Signal alerts fired: {fired}")
        except Exception as e:
            print(f"Alert firing error: {e}")

        # Run specialist agents after every rebuild
        try:
            from app.domain.agents.risk_agent import run as risk_run
            risk_result = risk_run()
            print(f"RiskAgent: {risk_result['risk_level']} — {len(risk_result.get('warnings', []))} warnings")
        except Exception as e:
            print(f"RiskAgent error: {e}")

        try:
            from app.domain.agents.briefing_agent import run as briefing_run
            briefing_run(user_id="default")
            print("BriefingAgent: morning briefing updated")
        except Exception as e:
            print(f"BriefingAgent error: {e}")

        try:
            from app.domain.agents.news_agent import run as news_run
            news_result = news_run()
            print(f"NewsAgent: {len(news_result.get('catalysts', {}))} catalysts found, {len(news_result.get('high_risk', []))} high-risk")
        except Exception as e:
            print(f"NewsAgent error: {e}")

        try:
            from app.domain.agents.guardian_agent import run as guardian_run
            g = guardian_run(user_id="default")
            print(f"GuardianAgent: watched={len(g.get('watched', []))}, alerts={len(g.get('alerts_fired', []))}")
        except Exception as e:
            print(f"GuardianAgent error: {e}")

        try:
            from app.domain.agents.outcome_agent import run as outcome_run
            o = outcome_run()
            print(f"OutcomeAgent: evaluated={len(o.get('evaluated', []))}, accuracy={o.get('accuracy', {}).get('win_rate', 'N/A')}")
        except Exception as e:
            print(f"OutcomeAgent error: {e}")

        try:
            from app.domain.agents.conflict_agent import run as conflict_run
            cf = conflict_run()
            print(f"ConflictAgent: conflicts={len(cf.get('conflicts', []))}, stress={cf.get('stress_level')}, score={cf.get('conflict_score')}")
        except Exception as e:
            print(f"ConflictAgent error: {e}")

        # Proactive reasoning engine — push insights for notable events
        try:
            from app.domain.core.proactive_engine import run_proactive_engine
            proactive_result = run_proactive_engine(cache, old_cache)
            print(f"Proactive engine: {proactive_result}")
        except Exception as e:
            print(f"Proactive engine error: {e}")

        # Clear Redis
        try:
            from app.infrastructure.cache.cache import _get_redis
            r = _get_redis()
            if r:
                for k in r.keys("*"):
                    r.delete(k)
                print("Redis cleared")
        except Exception as e:
            print(f"Redis clear skipped: {e}")

    except Exception as e:
        print(f"Cache rebuild failed: {e}")

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
