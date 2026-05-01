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


@router.get("/signals", response_model=List[SignalResponse], tags=["signals"])
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
    # bulk cache disabled — always read from JSON to ensure reasoning is fresh
    # Load from Redis first (survives deploys), fall back to JSON file
    cache = {}
    cache = {}
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
        try:
            results.append(SignalResponse(**{
                "symbol":          sig.get("symbol", sym),
                "display":         sig.get("display", sym),
                "name":            sig.get("name", sym),
                "type":            sig.get("type", ""),
                "icon":            sig.get("icon", ""),
                "direction":       sig.get("direction", "HOLD"),
                "probability":     sig.get("probability", 0.5),
                "confidence":      sig.get("confidence", "LOW"),
                "kelly_size":      sig.get("kelly_size", 0.0),
                "expected_value":  sig.get("expected_value", 0.0),
                "take_profit":     sig.get("take_profit", 0.0),
                "stop_loss":       sig.get("stop_loss", 0.0),
                "current_price":   sig.get("current_price", 0.0),
                "risk_reward":     sig.get("risk_reward", 0.0),
                "atr":             sig.get("atr", 0.0),
                "model_agreement": sig.get("model_agreement", 0.0),
                "top_features":    sig.get("top_features", []),
                "confluence":      sig.get("confluence", []),
                "confluence_score":str(sig.get("confluence_score", "")),
                "news":            sig.get("news", []),
                "reasoning":       sig.get("reasoning") or "",
                "generated_at":    sig.get("generated_at", ""),
                **{k: v for k, v in sig.items() if k not in {
                    "symbol","display","name","type","icon","direction","probability",
                    "confidence","kelly_size","expected_value","take_profit","stop_loss",
                    "current_price","risk_reward","atr","model_agreement","top_features",
                    "confluence","confluence_score","news","reasoning","generated_at"
                }}
            }))
        except Exception as _se:
            import logging
            logging.getLogger(__name__).warning(f"Signal parse error for {sym}: {_se}")
            continue
    if not type and not direction:
        set_cached("all_signals_list", [r.model_dump() for r in results], ttl=3600)
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
    sig = generate_signal(symbol, include_reasoning=True)

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
            sig["confluence_score"] = str(conf_int) if conf_int is not None else "0"
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


@router.get("/news/feed")
def get_global_news_feed():
    """Global news feed across key symbols for the News tab."""
    from app.domain.data.news import get_news, BULL_WORDS, BEAR_WORDS

    FEED_SYMBOLS = [
        ("BTC-USD", "CRYPTO"), ("ETH-USD", "CRYPTO"), ("SOL-USD", "CRYPTO"),
        ("BNB-USD", "CRYPTO"), ("XRP-USD", "CRYPTO"),
        ("SPY", "EQUITY"), ("QQQ", "EQUITY"), ("AAPL", "EQUITY"),
        ("NVDA", "EQUITY"), ("TSLA", "EQUITY"), ("MSFT", "EQUITY"),
        ("GC=F", "COMMODITY"), ("CL=F", "COMMODITY"), ("SI=F", "COMMODITY"),
        ("EURUSD=X", "FOREX"), ("GBPUSD=X", "FOREX"), ("DX-Y.NYB", "FOREX"),
        ("^GSPC", "MACRO"), ("^VIX", "MACRO"), ("^TNX", "MACRO"),
        ("^NSEI", "INDIA"), ("^BSESN", "INDIA"), ("RELIANCE.NS", "INDIA"),
        ("INFY.NS", "INDIA"), ("TCS.NS", "INDIA"),
    ]

    def _strong_sentiment(title: str, summary: str) -> str:
        """More aggressive sentiment scoring — counts all occurrences, not unique words."""
        text = f"{title} {summary}".lower()
        words = text.split()
        bulls = sum(1 for w in words if w.strip('.,!?;:') in BULL_WORDS)
        bears = sum(1 for w in words if w.strip('.,!?;:') in BEAR_WORDS)
        # Lower threshold: 1 signal word is enough if uncontested
        if bulls > bears: return "BULLISH"
        if bears > bulls: return "BEARISH"
        # Tie-break: check title alone (stronger signal)
        title_words = title.lower().split()
        t_bulls = sum(1 for w in title_words if w.strip('.,!?;:') in BULL_WORDS)
        t_bears = sum(1 for w in title_words if w.strip('.,!?;:') in BEAR_WORDS)
        if t_bulls > t_bears: return "BULLISH"
        if t_bears > t_bulls: return "BEARISH"
        return "NEUTRAL"

    def _infer_symbol_tag(title: str, summary: str, fallback: str) -> str:
        """Pick the most relevant ticker mentioned in the article."""
        text = f"{title} {summary}".upper()
        MENTIONS = [
            ("BITCOIN", "BTC"), ("BTC", "BTC"), ("ETHEREUM", "ETH"), ("ETH", "ETH"),
            ("SOLANA", "SOL"), ("XRP", "XRP"), ("NVIDIA", "NVDA"), ("NVDA", "NVDA"),
            ("APPLE", "AAPL"), ("AAPL", "AAPL"), ("TESLA", "TSLA"), ("TSLA", "TSLA"),
            ("MICROSOFT", "MSFT"), ("MSFT", "MSFT"), ("S&P 500", "SPX"), ("SPX", "SPX"),
            ("NASDAQ", "QQQ"), ("QQQ", "QQQ"), ("GOLD", "GC=F"), ("OIL", "CL=F"),
            ("NIFTY", "NIFTY"), ("SENSEX", "SENSEX"), ("RELIANCE", "RELIANCE"),
            ("INFOSYS", "INFY"), ("EUR/USD", "EURUSD"), ("DXY", "DXY"),
        ]
        for keyword, tag in MENTIONS:
            if keyword in text:
                return tag
        return fallback

    seen_titles = set()
    articles = []

    for symbol, category in FEED_SYMBOLS:
        try:
            items = get_news(symbol, limit=4)
            for item in items:
                key = item.title[:60].lower().strip()
                if key in seen_titles:
                    continue
                seen_titles.add(key)
                articles.append({
                    "title": item.title,
                    "summary": item.summary,
                    "source": item.source,
                    "url": item.url,
                    "sentiment": _strong_sentiment(item.title, item.summary),
                    "symbol": _infer_symbol_tag(item.title, item.summary, symbol),
                    "category": category,
                })
        except Exception:
            pass

    return articles
