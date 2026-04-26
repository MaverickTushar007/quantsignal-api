"""
api/routes/signals_ext.py
Extended signal endpoints: news, debug, mood, backtest, regime, stream.
"""
from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import StreamingResponse
from app.api.schemas import MarketMood, BacktestSummary
from app.api.routes.auth import require_pro
from app.domain.signal.service import generate_signal
from app.domain.billing.middleware import signal_gate
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


@router.get("/signals/{symbol}/stream", tags=["signals"])
async def stream_signal(symbol: str, _gate: dict = Depends(signal_gate)):
    """Perseus streaming endpoint — emits SSE events for each pipeline step."""
    import json
    import asyncio

    symbol = symbol.upper()
    if symbol not in TICKER_MAP:
        raise HTTPException(status_code=404, detail=f"Unknown symbol: {symbol}")

    async def generate():
        def emit(step: int, label: str, status: str, detail: str = ""):
            return f"data: {json.dumps({'step': step, 'label': label, 'status': status, 'detail': detail})}\n\n"

        try:
            yield emit(1, "Loading signal history", "running")
            await asyncio.sleep(0.1)
            history = []
            history_detail = "No past signals yet"
            try:
                from app.infrastructure.db.signal_history import get_evaluated_signals as get_signal_history
                history = get_signal_history(symbol, limit=5)
                history_detail = f"{len(history)} past signals found"
            except Exception:
                pass
            yield emit(1, "Loading signal history", "done", history_detail)

            yield emit(2, "Running technical analysis", "running")
            await asyncio.sleep(0.1)
            sig = None
            confluence_detail = "Confluence score computed"
            try:
                sig = generate_signal(symbol, include_reasoning=False)
                if sig:
                    score = sig.get("confluence_score", "?")
                    direction = sig.get("direction", "?")
                    confluence_detail = f"Confluence {score} — {direction}"
            except Exception:
                confluence_detail = "ML pipeline error"
            yield emit(2, "Running technical analysis", "done", confluence_detail)

            if sig is None:
                yield f"data: {json.dumps({'type': 'error', 'message': 'Signal generation failed'})}\n\n"
                return

            yield emit(3, "Calibrating confidence", "running")
            await asyncio.sleep(0.1)
            cal_prob = sig.get("probability", sig.get("raw_probability", 0))
            cal_detail = f"{cal_prob*100:.0f}% calibrated confidence"
            if sig.get("regime_suppressed"):
                cal_detail += " — suppressed"
            yield emit(3, "Calibrating confidence", "done", cal_detail)

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

            yield emit(5, "Running risk assessment", "running")
            await asyncio.sleep(0.1)
            try:
                energy = sig.get("energy_state", "unknown")
                regime = sig.get("regime", "unknown")
                rr = sig.get("risk_reward", "?")
                risk_detail = f"R/R {rr}:1 · {regime} regime · energy {energy}"
            except Exception:
                risk_detail = "Risk assessed"
            yield emit(5, "Running risk assessment", "done", risk_detail)

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
