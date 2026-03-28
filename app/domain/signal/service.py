from app.core.config import BASE_DIR
"""
core/signal_service.py
Main orchestrator — replaces your 786-line signal_generator.py.
Clean pipeline: fetch → features → ML → confluence → news → reasoning → result.
"""

import pandas as pd
from typing import Optional
from dataclasses import dataclass, asdict
from datetime import datetime, timezone

from app.domain.data.universe import TICKER_MAP
from app.domain.data.market import fetch_ohlcv
from app.domain.data.news import get_news, get_sentiment_score
from app.domain.ml.ensemble import predict, SignalResult
from app.domain.ml.features import build_features
from app.domain.reasoning.service import get_reasoning


@dataclass
class FullSignal:
    # Identity
    symbol:      str
    display:     str
    name:        str
    type:        str
    icon:        str
    # Signal
    direction:   str
    probability: float
    confidence:  str
    # Quant metrics
    kelly_size:     float
    expected_value: float
    take_profit:    float
    stop_loss:      float
    current_price:  float
    risk_reward:    float
    atr:            float
    # ML metadata
    model_agreement: float
    top_features:    list
    # Confluence
    confluence:       list
    confluence_score: str
    # News + reasoning
    news:       list
    reasoning:  str
    # Meta
    generated_at: str
    volume_ratio: float = 1.0


def _build_confluence(feat_row) -> list:
    """9-factor confluence scorecard from latest feature row."""
    rsi    = float(feat_row.get("RSI_14", 50))
    macdh  = float(feat_row.get("MACD_hist", 0))
    bbpct  = float(feat_row.get("BB_pct", 0.5)) * 100
    stoch  = float(feat_row.get("stoch_K", 50))
    volr   = float(feat_row.get("vol_ratio", 1))
    smacx  = float(feat_row.get("SMA_cross", 1)) > 1
    dsma20 = float(feat_row.get("dist_SMA20", 0)) > 0
    p52w   = float(feat_row.get("pos_52w", 0.5)) * 100
    mom5   = float(feat_row.get("mom_5d", 0)) * 100

    def sig(bull): return "BULLISH" if bull else "BEARISH"

    return [
        {"name": "RSI-14",       "value": f"{rsi:.0f} — {'Oversold' if rsi<35 else 'Overbought' if rsi>65 else 'Neutral'}",  "signal": sig(rsi < 50)},
        {"name": "MACD",         "value": f"{'Bullish' if macdh>0 else 'Bearish'} ({macdh:+.4f})",                            "signal": sig(macdh > 0)},
        {"name": "Bollinger",    "value": f"{bbpct:.0f}% ({'Upper' if bbpct>80 else 'Lower' if bbpct<20 else 'Mid'})",        "signal": sig(bbpct < 50)},
        {"name": "Stochastic %K","value": f"{stoch:.0f} — {'Overbought' if stoch>80 else 'Oversold' if stoch<20 else 'Neutral'}", "signal": sig(stoch < 50)},
        {"name": "Volume",       "value": f"{volr:.2f}x avg",                                                                  "signal": sig(volr > 1)},
        {"name": "SMA Cross",    "value": f"SMA20 {'above' if smacx else 'below'} SMA50",                                     "signal": sig(smacx)},
        {"name": "vs SMA20",     "value": f"Price {'above' if dsma20 else 'below'} SMA20",                                    "signal": sig(dsma20)},
        {"name": "52W Position", "value": f"{p52w:.0f}% ({'High' if p52w>70 else 'Low' if p52w<30 else 'Mid'})",              "signal": sig(p52w > 50)},
        {"name": "5D Momentum",  "value": f"{mom5:+.2f}% ROC",                                                                "signal": sig(mom5 > 0)},
    ]


def generate_signal(symbol: str, include_reasoning: bool = True) -> Optional[dict]:
    """
    Full pipeline for one ticker.
    Returns a dict ready to serialize to JSON — or None if data unavailable.
    """
    import json
    from pathlib import Path
    from app.infrastructure.cache.cache import get_cached, set_cached

    # --- LAYER 1: Redis cache (fastest) ---
    redis_key = f"signal:{symbol}"
    cached = get_cached(redis_key)
    if cached:
        if not include_reasoning and "reasoning" in cached:
            cached["reasoning"] = ""
        return cached

    # --- LAYER 2: Local JSON cache (Railway bypass) ---
    cache_path = BASE_DIR / "data/signals_cache.json"
    if cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text())
            if symbol in cache:
                sig = dict(cache[symbol])
                if not include_reasoning and "reasoning" in sig:
                    sig["reasoning"] = ""
                # Attach shock warning before returning
                try:
                    from app.domain.data.correlations import load_shock_cache
                    _shocks = load_shock_cache()
                    if symbol in _shocks:
                        sig['shock_warning'] = _shocks[symbol]
                except Exception:
                    pass
                set_cached(redis_key, sig, ttl=3600)  # warm Redis WITH mtf + shock
                return sig
        except Exception:
            pass

    meta = TICKER_MAP.get(symbol)
    if not meta:
        return None

    # 1. Fetch price data
    df = fetch_ohlcv(symbol, period="2y")
    if df is None:
        return None

    # 2. Get sentiment for ML blending
    sentiment = get_sentiment_score(symbol)

    # 3. ML signal
    ml: Optional[SignalResult] = predict(symbol, df, sentiment)
    if ml is None:
        return None

    # 4. Confluence scorecard
    feat       = build_features(df)
    latest_row = feat.iloc[-1].to_dict()
    confluence = _build_confluence(latest_row)
    bull_count = sum(1 for c in confluence if c["signal"] == "BULLISH")

    # 5. News
    news_items = get_news(symbol, limit=3)
    headlines  = [n.title for n in news_items]
    news_dicts = [{"title": n.title, "source": n.source,
                   "sentiment": n.sentiment, "url": n.url}
                  for n in news_items]

    # 6. LLM reasoning
    reasoning = ""
    if include_reasoning:
        reasoning = get_reasoning(
            ticker=symbol,
            name=meta["name"],
            direction=ml.direction,
            probability=ml.probability,
            confluence_bulls=bull_count,
            top_features=list(ml.top_features.keys()),
            news_headlines=headlines,
            current_price=ml.current_price,
            take_profit=ml.take_profit,
            stop_loss=ml.stop_loss,
            atr=ml.atr,
            model_agreement=ml.model_agreement,
        )

    result = asdict(FullSignal(
        symbol=symbol,
        display=meta["display"],
        name=meta["name"],
        type=meta["type"],
        icon=meta["icon"],
        direction=ml.direction,
        probability=ml.probability,
        confidence=ml.confidence,
        kelly_size=ml.kelly_size,
        expected_value=ml.expected_value,
        take_profit=ml.take_profit,
        stop_loss=ml.stop_loss,
        current_price=ml.current_price,
        risk_reward=ml.risk_reward,
        atr=ml.atr,
        model_agreement=ml.model_agreement,
        top_features=list(ml.top_features.keys()),
        confluence=confluence,
        confluence_score=f"{bull_count}/9 bullish",
        volume_ratio=ml.volume_ratio,
        news=news_dicts,
        reasoning=reasoning,
        generated_at=datetime.now(timezone.utc).isoformat(),
    ))
    from app.infrastructure.cache.cache import set_cached
    set_cached(f"signal:{symbol}", result, ttl=3600)
    # Attach insider trades (US stocks only)
    try:
        from app.domain.data.insider import get_insider_trades
        insider = get_insider_trades(symbol)
        if insider.get("available"):
            result["insider"] = insider
    except Exception as e:
        print(f"Insider error for {symbol}: {e}")

    # Attach MTF alignment
    try:
        from app.domain.data.mtf import fetch_mtf_features
        mtf = fetch_mtf_features(symbol)
        # Add daily direction to MTF score
        daily_bull = result.get('direction') == 'BUY'
        mtf['mtf_score_with_daily'] = mtf['mtf_score'] + (1 if daily_bull else 0)
        mtf['mtf_details']['1d'] = 'BULL' if daily_bull else 'BEAR'
        result['mtf'] = mtf
    except Exception as e:
        print(f"MTF error for {symbol}: {e}")
    # Attach energy state
    try:
        from app.domain.core.energy_detector import compute_market_energy
        energy = compute_market_energy(df)
        result["energy"] = energy
    except Exception as e:
        print(f"Energy detector error for {symbol}: {e}")

    # Attach earnings flag if applicable
    try:
        from app.domain.data.earnings import get_earnings_flag
        flag = get_earnings_flag(symbol)
        if flag:
            result["earnings_flag"] = flag
    except Exception:
        pass
    # Attach shock warning if this asset is flagged
    try:
        from app.domain.data.correlations import load_shock_cache
        shock_cache = load_shock_cache()
        if symbol in shock_cache:
            result['shock_warning'] = shock_cache[symbol]
    except Exception:
        pass

    return result
