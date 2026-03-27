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
from app.domain.data.universe import TICKERS, TICKER_MAP

router = APIRouter()


@router.get("/health", response_model=HealthResponse, tags=["system"])
async def health():
    from app.core.config import settings
    return HealthResponse(status="ok", version="1.0.0", env=settings.app_env)


@router.get("/signals", response_model=List[WatchlistItem], tags=["signals"])
def get_all_signals(
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

    # Load from file
    cache = {}
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
    reason: bool = Query(True, description="Include LLM reasoning"),
):
    """
    Full signal for one asset.
    Returned immediately — reasoning enqueued to Redis queue if not cached.
    """
    symbol = symbol.upper()
    if symbol not in TICKER_MAP:
        raise HTTPException(status_code=404, detail=f"Unknown symbol: {symbol}")

    sig = generate_signal(symbol, include_reasoning=False)
    if sig is None:
        raise HTTPException(status_code=503, detail=f"Could not generate signal for {symbol}")

    status = sig.get("reasoning_status", "")
    if reason and status not in ("pending", "complete"):
        enqueue_reasoning_job(symbol, sig)
    # Step 9: save signal for outcome tracking
    try:
        from app.domain.regime.detector import regime_multiplier
        from app.infrastructure.db.signal_history import _get_conn
        _rc, _db = _get_conn()
        _cur = _rc.cursor()
        _cur.execute("SELECT regime, return_20d, signal_bias FROM regime_cache WHERE symbol=%s", (symbol,))
        _row = _cur.fetchone()
        _rc.close()
        regime_data = {"regime": _row[0], "return_20d": _row[1], "signal_bias": _row[2]} if _row else {}
        sig["regime"] = regime_data.get("regime", "unknown")
        sig["signal_bias"] = regime_data.get("signal_bias", "")
        sig["regime_return_20d"] = regime_data.get("return_20d")
        multiplier = regime_multiplier(sig["regime"], sig.get("direction", ""))
        sig["regime_adjusted_probability"] = round(
            min(sig.get("probability", 0.5) * multiplier, 1.0), 3
        )
        regime = sig["regime"]
        direction = sig.get("direction", "")
        if regime in ("ranging", "bear") and direction == "BUY":
            sig["regime_suppressed"] = True
            sig["regime_suppression_reason"] = f"{regime} regime - BUY signal suppressed"
        elif regime == "bull" and direction == "SELL":
            sig["regime_suppressed"] = True
            sig["regime_suppression_reason"] = "bull regime - SELL signal suppressed"
        else:
            sig["regime_suppressed"] = False
    except Exception as e:
        sig["regime"] = "unknown"
        sig["regime_suppressed"] = False

    try:
        from app.infrastructure.db.signal_history import save_signal, is_open
        import logging; _log = logging.getLogger(__name__)
        # debug log moved to after pipeline — see save block
        if sig.get("direction") in ("BUY", "SELL") and not is_open(sig["symbol"]) and not sig.get("regime_suppressed"):
            raw_conf = sig.get("confluence_score", "")
            try:
                conf_int = int(str(raw_conf).split("/")[0]) if "/" in str(raw_conf) else None
            except Exception:
                conf_int = None
            sig["confluence_score"] = conf_int
            # Extract mtf_score integer from mtf dict
            mtf = sig.get("mtf", {})
            sig["mtf_score"] = mtf.get("mtf_score_with_daily") or mtf.get("mtf_score")
            # 1. Store raw probability
            raw_prob = sig.get("probability")
            sig["raw_probability"] = raw_prob

            # 2. Apply Platt calibration on raw probability
            try:
                from app.domain.signal.calibration import calibrate_probability
                calibrated = calibrate_probability(float(raw_prob)) if raw_prob is not None else raw_prob
            except Exception as _cal_e:
                import logging; logging.getLogger(__name__).warning(f"[calibration] skipped: {_cal_e}")
                calibrated = raw_prob

            # 3. Apply regime multiplier on top of calibrated probability
            regime_adj = sig.get("regime_adjusted_probability")
            if regime_adj is not None and calibrated is not None:
                # Recalculate regime_adjusted using calibrated base instead of raw
                from app.domain.regime.detector import regime_multiplier as get_multiplier
                multiplier = get_multiplier(sig.get("regime", "unknown"), sig.get("direction", ""))
                sig["regime_adjusted_probability"] = round(min(float(calibrated) * multiplier, 1.0), 4)
                sig["probability"] = sig["regime_adjusted_probability"]
            else:
                sig["probability"] = calibrated

            save_signal(sig)
    except Exception as _e:
        import logging; logging.getLogger(__name__).error(f'[save_signal FAILED] {_e}', exc_info=True)
    return sig




@router.get("/signals/{symbol}/reasoning", tags=["signals"])
async def get_signal_reasoning(symbol: str):
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
    try:
        r = _get_redis()
        if r:
            raw = r.get(_status_key(symbol))
            if raw:
                redis_status = _json.loads(raw).get("status")
    except Exception:
        pass

    status = redis_status or signal.get("reasoning_status", "pending")

    return {
        "symbol": symbol,
        "status": status,
        "reasoning": signal.get("reasoning") or None,
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
