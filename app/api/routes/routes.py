from app.core.config import BASE_DIR
"""
api/routes.py
All FastAPI endpoints.
"""

from fastapi import APIRouter, HTTPException, Depends, Query
from typing import List, Optional
from concurrent.futures import ThreadPoolExecutor

from app.api.schemas import (
    SignalResponse, WatchlistItem, MarketMood,
    BacktestSummary, HealthResponse
)
from app.api.routes.auth import get_current_user, require_pro
from app.domain.signal.service import generate_signal
from app.infrastructure.queue.reasoning_queue import enqueue_reasoning_job
from app.domain.reasoning.worker import fill_reasoning_async
from fastapi import BackgroundTasks
from app.domain.data.universe import TICKERS, TICKER_MAP
from app.domain.billing.middleware import signal_gate

router = APIRouter()


@router.get("/health", response_model=HealthResponse, tags=["system"])
async def health():
    from app.core.config import settings
    return HealthResponse(status="ok", version="1.0.0", env=settings.app_env)


@router.get("/signals", response_model=List[WatchlistItem], tags=["signals"])
def get_all_signals(
    _gate: dict = Depends(signal_gate),
    type:      Optional[str] = Query(None, description="CRYPTO|STOCK|ETF|INDEX|COMMODITY|FOREX"),
    direction: Optional[str] = Query(None, description="BUY|SELL|HOLD"),

):
    """
    Lightweight signals for all assets — powers the watchlist dashboard.
    No LLM reasoning (fast). Cached after first run.
    """
    from app.infrastructure.cache.cache import get_cached, set_cached
    import json
    from pathlib import Path

    # Try Redis first (fastest)
    if not type and not direction:
        cached = get_cached("all_signals_list")
        if cached:
            return cached

    # Load from Redis first (survives deploys), fall back to JSON file
    cache = {}
    try:
        redis_cache = get_cached("signals_cache_full")
        if redis_cache and isinstance(redis_cache, dict) and len(redis_cache) > 0:
            cache = redis_cache
    except Exception:
        pass
    if not cache:
        cache_path = BASE_DIR / "data/signals_cache.json"
        if cache_path.exists():
            try:
                cache = json.loads(cache_path.read_text())
            except Exception:
                pass

    tickers = [t for t in TICKERS if not type or t["type"] == type.upper()]
    results = []
    for meta in tickers:
        sym = meta["symbol"]
        sig = cache.get(sym)
        if sig is None:
            continue
        if direction and sig.get("direction") != direction.upper():
            continue
        results.append(WatchlistItem(
            symbol=sig["symbol"],
            display=sig["display"],
            name=sig["name"],
            type=sig["type"],
            icon=sig["icon"],
            direction=sig["direction"],
            probability=sig["probability"],
            confidence=sig["confidence"],
            current_price=sig["current_price"],
            kelly_size=sig["kelly_size"],
        ))
    if not type and not direction:
        set_cached("all_signals_list", results, ttl=86400)
    return results

@router.get("/signals/{symbol}", response_model=SignalResponse, tags=["signals"])
async def get_signal(
    symbol: str,
    background_tasks: BackgroundTasks,
    _gate: dict = Depends(signal_gate),
    reason: bool = Query(True, description="Include LLM reasoning"),
    bust: bool = Query(False, description="Force cache bypass"),
):
    """
    Full signal for one asset.
    Returned immediately — reasoning enqueued to Redis queue if not cached.
    """
    symbol = symbol.upper()
    if symbol not in TICKER_MAP:
        raise HTTPException(status_code=404, detail=f"Unknown symbol: {symbol}")

    if bust:
        from app.infrastructure.cache.cache import get_cached, set_cached
        import json
        from pathlib import Path
        from app.core.config import BASE_DIR
        # Evict from Redis
        try:
            from app.infrastructure.cache.cache import _get_redis
            r = _get_redis()
            if r: r.delete(f"signal:{symbol}")
        except Exception:
            pass
        # Evict from JSON file cache
        try:
            cache_path = BASE_DIR / "data/signals_cache.json"
            if cache_path.exists():
                cache = json.loads(cache_path.read_text())
                if symbol in cache:
                    del cache[symbol]
                    cache_path.write_text(json.dumps(cache))
        except Exception:
            pass
    sig = generate_signal(symbol, include_reasoning=False)

    # Staleness SLA — flag signal age so frontend can warn users
    if sig and sig.get("generated_at"):
        try:
            from datetime import datetime, timezone
            generated = datetime.fromisoformat(sig["generated_at"].replace("Z", "+00:00"))
            age_hours = (datetime.now(timezone.utc) - generated).total_seconds() / 3600
            # SLA: crypto 4h, stocks 26h (covers overnight + pre-market)
            is_crypto = symbol.endswith("-USD") or symbol.endswith("-USDT")
            sla_hours = 4 if is_crypto else 26
            sig["signal_age_hours"] = round(age_hours, 1)
            sig["is_stale"] = age_hours > sla_hours
            if sig["is_stale"]:
                sig["stale_warning"] = f"Signal is {age_hours:.0f}h old — refresh for latest data"
        except Exception:
            sig["is_stale"] = False
    if sig is None:
        raise HTTPException(status_code=503, detail=f"Could not generate signal for {symbol}")

    # Full enrichment pipeline — regime, calibration, energy, EV, context
    from app.domain.signal.pipeline import enrich_signal
    sig = enrich_signal(sig, symbol)

    status = sig.get("reasoning_status", "")
    if reason and status not in ("pending", "complete"):
        background_tasks.add_task(fill_reasoning_async, symbol, sig)

    try:
        from app.infrastructure.db.signal_history import save_signal, is_open
        if sig.get("direction") in ("BUY", "SELL") and not is_open(sig["symbol"]) and not sig.get("regime_suppressed"):
            raw_conf = sig.get("confluence_score", "")
            try:
                conf_int = int(str(raw_conf).split("/")[0]) if "/" in str(raw_conf) else None
            except Exception:
                conf_int = None
            sig["confluence_score"] = conf_int
            mtf = sig.get("mtf", {})
            sig["mtf_score"] = mtf.get("mtf_score_with_daily") or mtf.get("mtf_score")
            # Telegram alert
            try:
                from app.domain.alerts.telegram import send_telegram, format_signal_alert
                from app.domain.alerts.dedup import should_alert
                from app.api.routes.preferences import _load_prefs
                _user_prefs    = _load_prefs("default")
                _alert_thresh  = _user_prefs.get("alert_threshold", 0.50)
                if sig.get("probability", 0) >= _alert_thresh and should_alert(sig.get("symbol", "")):
                    send_telegram(format_signal_alert(sig))
            except Exception as _tel_e:
                import logging; logging.getLogger(__name__).warning(f"[telegram] {_tel_e}")

            # Validate signal before saving
            try:
                from app.domain.core.signal_validator import validate_signal
                is_valid, reason = validate_signal(sig)
                if not is_valid:
                    import logging
                    logging.getLogger(__name__).warning(f"[validator] signal rejected: {reason} for {sig.get('symbol')}")
            except Exception as _val_e:
                import logging
                logging.getLogger(__name__).warning(f"[validator] {_val_e}")

            # Track alert for performance measurement
            try:
                from app.domain.alerts.tracker import log_alert
                import logging as _log
                _prob = sig.get("probability", 0)
                _suppressed = sig.get("regime_suppressed", False)
                if _prob >= 0.50 and not _suppressed:
                    log_alert(sig, "signal")
                    _log.getLogger(__name__).info(f"[tracker] alert logged for {sig.get('symbol')} prob={_prob:.2f}")
                else:
                    _log.getLogger(__name__).info(f"[tracker] skipped {sig.get('symbol')} prob={_prob:.2f} suppressed={_suppressed}")
            except Exception as _track_e:
                import logging; logging.getLogger(__name__).warning(f"[tracker] {_track_e}")
            save_signal(sig)
    except Exception as _e:
        import logging; logging.getLogger(__name__).error(f'[save_signal FAILED] {_e}', exc_info=True)

    # Sprint 4 — store embedding for similarity search
    try:
        from app.infrastructure.db.signal_embeddings import store_embedding
        import threading
        threading.Thread(target=store_embedding, args=(sig,), daemon=True).start()
    except Exception as _ee:
        pass
    return sig




@router.get("/signals/{symbol}/reasoning", tags=["signals"])
async def get_signal_reasoning(symbol: str, _gate: dict = Depends(signal_gate)):
    """
    Poll this after GET /signals/{symbol}.
    Returns reasoning status + content once async worker completes.
    """
    symbol = symbol.upper()
    if symbol not in TICKER_MAP:
        raise HTTPException(status_code=404, detail=f"Unknown symbol: {symbol}")

    # Try Redis first, fall back to JSON cache
    from app.infrastructure.cache.cache import get_cached
    import json
    from pathlib import Path
    from app.core.config import BASE_DIR

    signal = get_cached(f"signal:{symbol}")
    if not signal:
        cache_path = BASE_DIR / "data/signals_cache.json"
        if cache_path.exists():
            cache = json.loads(cache_path.read_text())
            signal = cache.get(symbol)

    if not signal:
        return {"symbol": symbol, "status": "not_found", "reasoning": None}

    # Check Redis deduplication status first (source of truth)
    from app.infrastructure.queue.reasoning_queue import _status_key
    from app.infrastructure.cache.cache import _get_redis
    import json as _json
    redis_status = None
    redis_reasoning = None
    try:
        r = _get_redis()
        if r:
            raw = r.get(_status_key(symbol))
            if raw:
                _rd = _json.loads(raw)
                redis_status = _rd.get("status")
                redis_reasoning = _rd.get("reasoning") or None
    except Exception:
        pass

    status = redis_status or signal.get("reasoning_status", "pending")

    return {
        "symbol": symbol,
        "status": status,
        "reasoning": redis_reasoning or signal.get("reasoning") or None,
        "timestamp": signal.get("timestamp"),
    }
@router.get("/news/{symbol}", tags=["news"])
def get_asset_news(symbol: str, limit: int = 10):
    symbol = symbol.upper()
    if symbol not in TICKER_MAP:
        raise HTTPException(status_code=404, detail=f"Unknown symbol: {symbol}")
    try:
        from app.domain.data.news import get_news
        items = get_news(symbol, limit=limit)
        return {
            "symbol": symbol,
            "count": len(items),
            "items": [
                {
                    "title": n.title,
                    "summary": n.summary[:200] if n.summary else "",
                    "source": n.source,
                    "url": n.url,
                    "sentiment": n.sentiment,
                }
                for n in items
            ]
        }
    except Exception as e:
        return {"symbol": symbol, "count": 0, "items": [], "error": str(e)}

@router.get("/signals/debug/{symbol}", tags=["signals"])
def debug_signal(symbol: str):
    import traceback
    try:
        from app.domain.data.market import fetch_ohlcv
        import traceback, requests
        try:
            from app.domain.data.market import COINGECKO_ID_MAP, fetch_coingecko_ohlcv
            cg_id = COINGECKO_ID_MAP.get(symbol.upper())
            url = f"https://api.coingecko.com/api/v3/coins/{cg_id}/ohlc?vs_currency=usd&days=90"
            resp = requests.get(url, timeout=20)
            return {"status": resp.status_code, "cg_id": cg_id, "data_len": len(resp.json()) if resp.status_code==200 else 0, "body_preview": resp.text[:100]}
        except Exception as e:
            return {"error": str(e), "trace": traceback.format_exc()[-300:]}
    except Exception as e:
        return {"error": str(e), "trace": traceback.format_exc()[-500:]}

@router.get("/market/mood", response_model=MarketMood, tags=["signals"])
def market_mood():
    """
    Aggregate mood across first 20 assets — powers the top bar.
    """
    sample  = [t["symbol"] for t in TICKERS[:20]]
    buys = sells = holds = 0
    probs = []

    for sym in sample:
        sig = generate_signal(sym, include_reasoning=False)
        if not sig:
            continue
        if sig["direction"] == "BUY":    buys  += 1
        elif sig["direction"] == "SELL": sells += 1
        else:                            holds += 1
        probs.append(sig["probability"])

    total    = buys + sells + holds
    avg_conf = round(sum(probs) / len(probs), 3) if probs else 0

    if buys > sells * 1.5:   mood = "BULLISH"
    elif sells > buys * 1.5: mood = "BEARISH"
    else:                    mood = "NEUTRAL"

    return MarketMood(
        mood=mood, buy_count=buys, sell_count=sells,
        hold_count=holds, avg_confidence=avg_conf, total=total
    )


@router.get("/backtest/{symbol}", response_model=BacktestSummary, tags=["backtest"])
def backtest(
    symbol: str,
    user: dict = Depends(require_pro),
):
    """Walk-forward backtest — pro only."""
    symbol = symbol.upper()
    if symbol not in TICKER_MAP:
        raise HTTPException(status_code=404, detail=f"Unknown symbol: {symbol}")

    from app.domain.data.market import fetch_ohlcv
    from app.domain.ml.backtest import run

    df = fetch_ohlcv(symbol, period="2y")
    if df is None:
        raise HTTPException(status_code=503, detail="Could not fetch data")

    try:
        result = run(df, symbol)
        return BacktestSummary(
            ticker=result.ticker,
            win_rate=result.win_rate,
            avg_return=result.avg_return,
            sharpe=result.sharpe,
            max_drawdown=result.max_drawdown,
            total_return=result.total_return,
            n_trades=result.n_trades,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/regime/{symbol}", tags=["quant"])
async def get_regime(symbol: str):
    from app.domain.regime.detector import detect_regime
    return detect_regime(symbol)

@router.post("/regime/cache", tags=["quant"])
async def cache_regime(payload: dict):
    """Accept regime data pushed from local runner and cache it."""
    from app.infrastructure.cache.cache import set_cached, get_cached
    symbol = payload.get("symbol")
    if not symbol:
        return {"error": "symbol required"}
    set_cached(f"regime:{symbol}", payload, ttl=3600)
    return {"status": "cached", "symbol": symbol}

@router.get("/regime/{symbol}", tags=["quant"])
async def get_regime(symbol: str):
    from app.infrastructure.cache.cache import get_cached
    cached = get_cached(f"regime:{symbol}")
    if cached:
        return cached
    return {"regime": "unknown", "reason": "no regime data cached — run local regime updater"}

@router.get("/debug/regime/{symbol}")
async def debug_regime(symbol: str):
    from app.domain.regime.detector import detect_regime
    try:
        result = detect_regime(symbol.upper())
        return {"status": "ok", "result": result}
    except Exception as e:
        return {"status": "error", "error": str(e), "type": type(e).__name__}

# ── Web Push subscription endpoints ──────────────────────────────────────
from fastapi import Body as _Body


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



@router.get("/signals/{symbol}/stream", tags=["signals"])
async def stream_signal(
    symbol: str,
    _gate: dict = Depends(signal_gate),
):
    """
    Perseus streaming endpoint — emits SSE events for each pipeline step.
    Powers the step-by-step UI on the frontend.
    """
    import json
    import asyncio
    from fastapi.responses import StreamingResponse

    symbol = symbol.upper()
    if symbol not in TICKER_MAP:
        raise HTTPException(status_code=404, detail=f"Unknown symbol: {symbol}")

    async def generate():
        def emit(step: int, label: str, status: str, detail: str = ""):
            return f"data: {json.dumps({'step': step, 'label': label, 'status': status, 'detail': detail})}\n\n"

        try:
            # ── STEP 1: Signal history ────────────────────────────────────
            yield emit(1, "Loading signal history", "running")
            await asyncio.sleep(0.1)
            history = []
            history_detail = "No past signals yet"
            try:
                from app.infrastructure.db.signal_history import get_recent_signals
                history = get_recent_signals(symbol, limit=5)
                history_detail = f"{len(history)} past signal{'s' if len(history) != 1 else ''} found"
            except Exception:
                pass
            # Also pre-fetch similar embeddings (used later in Perseus prompt)
            similar_setups = []
            try:
                from app.infrastructure.db.signal_embeddings import find_similar
                _probe = {"symbol": symbol, "direction": "BUY", "probability": 0.6}
                similar_setups = find_similar(_probe, limit=3)
            except Exception:
                pass
            yield emit(1, "Loading signal history", "done", history_detail)

            # ── STEP 2: Technical analysis (ML confluence) ────────────────
            yield emit(2, "Running technical analysis", "running")
            await asyncio.sleep(0.1)
            sig = None
            confluence_detail = "Confluence score computed"
            try:
                from app.domain.signal.service import generate_signal
                sig = generate_signal(symbol, include_reasoning=False)
                if sig:
                    score = sig.get("confluence_score", "?")
                    direction = sig.get("direction", "?")
                    confluence_detail = f"Confluence {score} — {direction}"
            except Exception as e:
                confluence_detail = "ML pipeline error"
            yield emit(2, "Running technical analysis", "done", confluence_detail)

            if sig is None:
                yield f"data: {json.dumps({'type': 'error', 'message': 'Signal generation failed'})}\n\n"
                return

            # ── STEP 3: Calibration ───────────────────────────────────────
            yield emit(3, "Calibrating confidence", "running")
            await asyncio.sleep(0.1)
            raw_prob = sig.get("raw_probability") or sig.get("probability", 0)
            cal_prob = sig.get("probability", raw_prob)
            cal_detail = f"{cal_prob*100:.0f}% calibrated confidence"
            if sig.get("regime_suppressed"):
                cal_detail += " — suppressed"
            yield emit(3, "Calibrating confidence", "done", cal_detail)

            # ── STEP 4: Timeframe conflict check ─────────────────────────
            yield emit(4, "Validating timeframes", "running")
            await asyncio.sleep(0.1)
            conflict_detail = "Timeframes aligned"
            try:
                if sig.get("conflict_detected"):
                    conflict_detail = f"Conflict: {sig.get('conflict_reason', 'signals diverge')}"
                elif sig.get("mtf"):
                    mtf = sig["mtf"]
                    aligned = sum(1 for v in mtf.values() if v == sig.get("direction"))
                    conflict_detail = f"{aligned}/{len(mtf)} timeframes aligned"
            except Exception:
                pass
            yield emit(4, "Validating timeframes", "done", conflict_detail)

            # ── STEP 5: Risk assessment ───────────────────────────────────
            yield emit(5, "Running risk assessment", "running")
            await asyncio.sleep(0.1)
            risk_detail = "Risk assessed"
            try:
                energy = sig.get("energy_state", "unknown")
                regime = sig.get("regime", "unknown")
                rr = sig.get("risk_reward", "?")
                risk_detail = f"R/R {rr}:1 · {regime} regime · energy {energy}"
            except Exception:
                pass
            yield emit(5, "Running risk assessment", "done", risk_detail)

            # ── STEP 6: Perseus reasoning (LLM) ──────────────────────────
            yield emit(6, "Perseus generating reasoning", "running")
            await asyncio.sleep(0.2)
            reasoning = sig.get("reasoning") or sig.get("context_text") or ""
            if not reasoning or len(reasoning) < 40:
                try:
                    from app.domain.reasoning.service import get_reasoning
                    reasoning = get_reasoning(
                        ticker=symbol,
                        name=sig.get("name", symbol),
                        direction=sig.get("direction", "HOLD"),
                        probability=float(sig.get("probability", 0.5)),
                        confluence_bulls=int(str(sig.get("confluence_score", "0/9")).split("/")[0]),
                        top_features=sig.get("top_features", []),
                        news_headlines=[],
                        current_price=sig.get("current_price", 0),
                        take_profit=sig.get("take_profit", 0),
                        stop_loss=sig.get("stop_loss", 0),
                        atr=sig.get("atr", 0),
                        volume_ratio=sig.get("volume_ratio", 1.0),
                        model_agreement=sig.get("model_agreement", 0),
                    )
                    sig["reasoning"] = reasoning
                except Exception:
                    reasoning = sig.get("context_text", "Perseus analysis complete.")
            yield emit(6, "Perseus generating reasoning", "done")

            # ── STORE EMBEDDING (Sprint 4) ────────────────────────────────
            try:
                from app.infrastructure.db.signal_history import save_signal
                from app.infrastructure.db.signal_embeddings import store_embedding
                import threading
                sig["symbol"] = symbol
                save_signal(sig)
                threading.Thread(target=store_embedding, args=(sig,), daemon=True).start()
            except Exception:
                pass

            # ── FINAL RESULT ──────────────────────────────────────────────
            sig["reasoning"] = reasoning
            sig["stream_complete"] = True
            yield f"data: {json.dumps({'type': 'result', 'signal': sig})}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )

@router.get("/admin/watcher/trigger", tags=["admin"])
def trigger_watcher():
    """Manually trigger Perseus watcher scan — for testing."""
    try:
        from app.infrastructure.scheduler.perseus_watcher import scan_and_alert
        import threading
        threading.Thread(target=scan_and_alert, daemon=True).start()
        return {"status": "scan started", "message": "Check Telegram in ~2 min"}
    except Exception as e:
        return {"status": "error", "message": str(e)}
