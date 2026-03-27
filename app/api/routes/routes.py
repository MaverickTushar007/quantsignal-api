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
        from app.domain.regime.detector import detect_regime, regime_multiplier
        regime_data = detect_regime(symbol)
        sig["regime"] = regime_data.get("regime", "unknown")
        sig["signal_bias"] = regime_data.get("signal_bias", "")
        sig["regime_return_20d"] = regime_data.get("return_20d")
        multiplier = regime_multiplier(sig["regime"], sig.get("direction", ""))
        sig["regime_adjusted_probability"] = round(
            min(sig.get("probability", 0.5) * multiplier, 1.0), 3
        )
    except Exception as e:
        sig["regime"] = "unknown"

    try:
        from app.infrastructure.db.signal_history import save_signal, is_open
        if sig.get("direction") in ("BUY", "SELL") and not is_open(sig["symbol"]):
            raw_conf = sig.get("confluence_score", "")
            try:
                conf_int = int(str(raw_conf).split("/")[0]) if "/" in str(raw_conf) else None
            except Exception:
                conf_int = None
            sig["confluence_score"] = conf_int
            save_signal(sig)
    except Exception:
        pass
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
