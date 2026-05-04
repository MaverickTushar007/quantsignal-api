"""
api/routes/market_context.py
Market context endpoints: news, mood, backtest, regime.
"""
from fastapi import APIRouter, HTTPException, Depends
from app.api.schemas import MarketMood, BacktestSummary
from app.api.routes.auth import require_pro
from app.domain.signal.service import generate_signal
from app.domain.data.universe import TICKERS, TICKER_MAP

router = APIRouter()

@router.get("/news/backtest-summary")
def news_backtest_summary():
    """
    Aggregate stats on news sentiment prediction accuracy.
    Shows accuracy per horizon (1h/4h/24h/5d) and per source.
    MUST be before /news/{symbol} route to avoid catch-all conflict.
    Ref: Feuerriegel (2016) https://arxiv.org/abs/1807.06824
    """
    from app.domain.data.news_backtest import get_backtest_summary
    return get_backtest_summary()


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


@router.get("/market/mood", response_model=MarketMood, tags=["signals"])
def market_mood():
    """Aggregate mood across first 20 assets — powers the top bar."""
    sample = [t["symbol"] for t in TICKERS[:20]]
    buys = sells = holds = 0
    probs = []

    for sym in sample:
        sig = generate_signal(sym, include_reasoning=False)
        if not sig:
            continue
        if sig["direction"] == "BUY":
            buys += 1
        elif sig["direction"] == "SELL":
            sells += 1
        else:
            holds += 1
        probs.append(sig["probability"])

    total = buys + sells + holds
    avg_conf = round(sum(probs) / len(probs), 3) if probs else 0

    if buys > sells * 1.5:
        mood = "BULLISH"
    elif sells > buys * 1.5:
        mood = "BEARISH"
    else:
        mood = "NEUTRAL"

    return MarketMood(
        mood=mood, buy_count=buys, sell_count=sells,
        hold_count=holds, avg_confidence=avg_conf, total=total
    )


@router.get("/backtest/{symbol}", response_model=BacktestSummary, tags=["backtest"])
def backtest(symbol: str, user: dict = Depends(require_pro)):
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
    from app.infrastructure.cache.cache import get_cached
    cached = get_cached(f"regime:{symbol}")
    if cached:
        return cached
    # Fallback to macro_regime.py (FRED-based classifier)
    try:
        from app.domain.data.macro_regime import get_macro_regime
        result = get_macro_regime()
        return {
            "regime": result["regime"],
            "regime_score": result["regime_score"],
            "prob_multiplier": result["prob_multiplier"],
            "details": result["details"],
            "source": "macro_regime_fallback",
        }
    except Exception as e:
        return {"regime": "unknown", "reason": f"macro_regime fallback failed: {e}"}


@router.get("/debug/regime/{symbol}")
async def debug_regime(symbol: str):
    from app.domain.regime.detector import detect_regime
    try:
        result = detect_regime(symbol.upper())
        return {"status": "ok", "result": result}
    except Exception as e:
        return {"status": "error", "error": str(e), "type": type(e).__name__}




# ── COT + News Backtest endpoints (Phase 1) ───────────────────────────────────


@router.get("/cot/{symbol}")
def get_cot_for_symbol(symbol: str):
    """
    CFTC COT positioning for a symbol.
    """
    from app.domain.data.cot import get_cot_signal
    return get_cot_signal(symbol.upper())


@router.get("/cot")
def get_all_cot():
    """
    CFTC COT signals for all covered symbols.
    """
    from app.domain.data.cot import get_all_cot_signals
    return get_all_cot_signals()


# ── OFI + Liquidation endpoints (Phase 2) ────────────────────────────────────

@router.get("/ofi/{symbol}")
def get_ofi(symbol: str):
    """
    Order Flow Imbalance from OKX L2 order book.
    Crypto only — returns neutral defaults for equities/forex.
    Ref: arXiv 2508.06788, dm13450 OFI blog
    """
    from app.domain.data.order_flow import get_ofi_features
    return get_ofi_features(symbol.upper())


@router.get("/liquidations/{symbol}")
def get_liquidations(symbol: str):
    """
    Forced liquidation pressure from Binance futures.
    Crypto only. liq_signal: +1=short squeeze, -1=long cascade, 0=neutral.
    Ref: Binance allForceOrders API, CoinGlass liquidation heatmap
    """
    from app.domain.data.liquidation_heatmap import get_liquidation_signal
    return get_liquidation_signal(symbol.upper())
