"""
api/routes.py
All FastAPI endpoints.
"""

from fastapi import APIRouter, HTTPException, Depends, Query
from typing import List, Optional
from concurrent.futures import ThreadPoolExecutor

from api.schemas import (
    SignalResponse, WatchlistItem, MarketMood,
    BacktestSummary, HealthResponse
)
from api.auth import get_current_user, require_pro
from core.signal_service import generate_signal
from data.universe import TICKERS, TICKER_MAP

router = APIRouter()


@router.get("/health", response_model=HealthResponse, tags=["system"])
async def health():
    from core.config import settings
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
    tickers = [
        t for t in TICKERS
        if not type or t["type"] == type.upper()
    ]

    results = []
    for meta in tickers:
        sig = generate_signal(meta["symbol"], include_reasoning=False)
        if sig is None:
            continue
        if direction and sig["direction"] != direction.upper():
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

    return results


@router.get("/signals/{symbol}", response_model=SignalResponse, tags=["signals"])
def get_signal(
    symbol:  str,
    reason:  bool = Query(True, description="Include LLM reasoning"),

):
    """
    Full signal for one asset — includes confluence, news, LLM reasoning.
    LLM reasoning only for pro tier.
    """
    symbol = symbol.upper()
    if symbol not in TICKER_MAP:
        raise HTTPException(status_code=404, detail=f"Unknown symbol: {symbol}")

    include_reasoning = reason
    sig = generate_signal(symbol, include_reasoning=include_reasoning)

    if sig is None:
        raise HTTPException(status_code=503, detail=f"Could not generate signal for {symbol}")

    return sig


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

    from data.market import fetch_ohlcv
    from ml.backtest import run

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
