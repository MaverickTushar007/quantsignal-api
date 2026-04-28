"""
api/routes/tasks.py
Background task logic for cache rebuild, retrain, alerts, guardian.
Imported by cron.py HTTP handlers.
"""
from app.core.config import BASE_DIR
import os, json, time, threading
from pathlib import Path

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
        from app.domain.data.universe import TICKERS, REBUILD_TICKERS
        from app.domain.signal.service import generate_signal

        # Split into 4 parallel worker groups — use REBUILD_TICKERS (25 core assets)
        # Process all tickers in batches of 25 to avoid OOM
        _RT = TICKERS
        BATCH_SIZE = 25
        _batches = [_RT[i:i+BATCH_SIZE] for i in range(0, len(_RT), BATCH_SIZE)]
        GROUPS = {}
        for i, batch in enumerate(_batches):
            GROUPS[f"BATCH_{i+1}"] = batch

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
        with ThreadPoolExecutor(max_workers=1) as executor:
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
        # Invalidate stale all_signals_list so /signals serves fresh data
        try:
            from app.infrastructure.cache.cache import _get_redis
            r = _get_redis()
            if r:
                r.delete("all_signals_list")
                print("[cron] invalidated all_signals_list Redis cache")
        except Exception as _re:
            print(f"[cron] Redis invalidation failed: {_re}")
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

        # Save signals to history DB — max once per 4 hours per symbol
        try:
            from app.infrastructure.db.signal_history import init_db, save_signal, _get_conn
            init_db()
            saved = 0
            skipped = 0
            _con, _db = _get_conn()
            _cur = _con.cursor()
            for sym, sig in cache.items():
                if sig.get("direction") not in ("BUY", "SELL"):
                    continue
                try:
                    if _db == "pg":
                        _cur.execute(
                            "SELECT COUNT(*) FROM signal_history WHERE symbol=%s "
                            "AND generated_at > NOW() - INTERVAL '4 hours'",
                            (sym,)
                        )
                    else:
                        _cur.execute(
                            "SELECT COUNT(*) FROM signal_history WHERE symbol=? "
                            "AND generated_at > datetime('now', '-4 hours')",
                            (sym,)
                        )
                    _recent = _cur.fetchone()[0]
                    if _recent == 0:
                        save_signal({**sig, "symbol": sym})
                        saved += 1
                    else:
                        skipped += 1
                except Exception as _se:
                    print(f"Signal save error for {sym}: {_se}")
            _con.close()
            print(f"Signal history: {saved} saved, {skipped} skipped (recent)")
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
                for k in r.keys("signal:*"):
                    r.delete(k)
                print("Redis signal keys cleared")
        except Exception as e:
            print(f"Redis clear skipped: {e}")

    except Exception as e:
        print(f"Cache rebuild failed: {e}")
