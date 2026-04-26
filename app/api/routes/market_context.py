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
    import traceback, requests
    try:
        from app.domain.data.market import COINGECKO_ID_MAP
        cg_id = COINGECKO_ID_MAP.get(symbol.upper())
        url = f"https://api.coingecko.com/api/v3/coins/{cg_id}/ohlc?vs_currency=usd&days=90"
        resp = requests.get(url, timeout=20)
        return {
            "status": resp.status_code,
            "cg_id": cg_id,
            "data_len": len(resp.json()) if resp.status_code == 200 else 0,
            "body_preview": resp.text[:100],
        }
    except Exception as e:
        return {"error": str(e), "trace": traceback.format_exc()[-500:]}


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
    return {"regime": "unknown", "reason": "no regime data cached — run local regime updater"}


@router.get("/debug/regime/{symbol}")
async def debug_regime(symbol: str):
    from app.domain.regime.detector import detect_regime
    try:
        result = detect_regime(symbol.upper())
        return {"status": "ok", "result": result}
    except Exception as e:
        return {"status": "error", "error": str(e), "type": type(e).__name__}
