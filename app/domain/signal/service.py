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
from app.domain.signal.confluence_v2 import build_confluence_v2, enforce_consistency_v2
from app.domain.core.circuit_breaker_v2 import CircuitBreaker, evaluate_and_update_outcomes


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
    market_open:    bool = True
    earnings_flag:      dict = None
    macro_event_today:  dict = None
    event_adjustment:   dict = None
    data_warnings:   list = None
    volume_ratio: float = 1.0
    session_info:  str = "nse (×1.0)"




def generate_signal(symbol: str, include_reasoning: bool = True, bypass_cache: bool = False) -> Optional[dict]:
    """
    Full pipeline for one ticker.
    Returns a dict ready to serialize to JSON — or None if data unavailable.
    """
    import json
    from pathlib import Path
    from app.infrastructure.cache.cache import get_cached, set_cached

    # --- LAYER 1: Redis cache (fastest) ---
    redis_key = f"signal:{symbol}"
    if bypass_cache:
        cached = None
    else:
        cached = get_cached(redis_key)
    if cached:
        if not include_reasoning and "reasoning" in cached:
            cached["reasoning"] = ""
        # Add energy if missing from cached signal
        if "energy" not in cached or cached.get("energy") is None:
            try:
                from app.domain.core.energy_detector import compute_energy_state
                _df = fetch_ohlcv(symbol, period="2y")
                if _df is not None:
                    cached["energy"] = compute_energy_state(_df)
            except Exception:
                pass
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

    # 5d. Apply event-day adjustments to TP/SL and Kelly size
    macro_event_today = None
    event_adj = {"atr_multiplier": 1.0, "kelly_reduction": 1.0, "event_type": None}
    try:
        from app.domain.data.event_adjustments import get_event_adjustments
        event_adj = get_event_adjustments(symbol, macro_event_today)
    except Exception:
        pass

    # 5e. Liquidity-aware TP/SL snapping (crypto only)
    liquidity_clusters = None
    try:
        from app.domain.data.liquidity_levels import get_liquidity_clusters
        liquidity_clusters = get_liquidity_clusters(symbol, 0)  # price filled after ml
    except Exception:
        pass

    # Cross-source validation — reject bad/stale data before it reaches the model
    try:
        from app.domain.data.multi_source import validate_ohlcv
        is_valid, data_warnings = validate_ohlcv(df, symbol)
        if not is_valid:
            logger.warning(f"[signal] {symbol} rejected — {data_warnings}")
            return None
        if data_warnings:
            logger.warning(f"[signal] {symbol} data warnings: {data_warnings}")
    except Exception as _ve:
        data_warnings = []
    if df is None:
        return None

    # 2. Get sentiment for ML blending
    sentiment = get_sentiment_score(symbol)

    # 3. ML signal
    ml: Optional[SignalResult] = predict(symbol, df, sentiment)
    # Apply liquidity cluster snapping to TP/SL (crypto only)
    if ml is not None and liquidity_clusters is None:
        try:
            from app.domain.data.liquidity_levels import get_liquidity_clusters
            liquidity_clusters = get_liquidity_clusters(symbol, ml.current_price)
        except Exception:
            pass

    if ml is not None and liquidity_clusters is not None:
        try:
            import dataclasses
            from app.domain.data.liquidity_levels import snap_to_liquidity
            new_tp, new_sl, snap_info = snap_to_liquidity(
                ml.direction, ml.current_price,
                ml.take_profit, ml.stop_loss,
                liquidity_clusters,
            )
            if snap_info:
                ml = dataclasses.replace(ml, take_profit=new_tp, stop_loss=new_sl)
        except Exception:
            pass

    # Apply event-day ATR multiplier to TP/SL and Kelly reduction
    if ml is not None and event_adj["atr_multiplier"] != 1.0:
        import dataclasses
        mult = event_adj["atr_multiplier"]
        kelly_mult = event_adj["kelly_reduction"]
        close = ml.current_price
        if ml.direction == "BUY":
            new_tp = close + (ml.take_profit - close) * mult
            new_sl = close - (close - ml.stop_loss) * mult
        else:
            new_tp = close - (close - ml.take_profit) * mult
            new_sl = close + (ml.stop_loss - close) * mult
        ml = dataclasses.replace(
            ml,
            take_profit=round(new_tp, 4),
            stop_loss=round(new_sl, 4),
            kelly_size=round(ml.kelly_size * kelly_mult, 2),
        )

    if ml is None:
        return None

    # 4. Confluence scorecard
    feat       = build_features(df)
    latest_row = feat.iloc[-1].to_dict()
    asset_type = meta.get("type", "equity")
    confluence, bull_count, score_label, session_info = build_confluence_v2(
        latest_row, df, asset_type
    )
    session_mult = 1.0

    # 4b. Enforce consistency: confluence → direction → agreement
    enforced_dir, enforced_prob, confluence_agreement = enforce_consistency_v2(
        ml.direction, ml.probability, bull_count, session_mult
    )
    # Patch ml fields so reasoning + all downstream use consistent values
    ml.direction       = enforced_dir
    ml.probability     = enforced_prob
    ml.model_agreement = confluence_agreement
    # Recalculate confidence from enforced probability
    p = enforced_prob
    ml.confidence = "HIGH" if p > 0.65 or p < 0.35 else "MEDIUM" if p > 0.55 or p < 0.45 else "LOW"

    # 4c. Apply liquidity cluster snapping AFTER direction is enforced
    if liquidity_clusters is None:
        try:
            from app.domain.data.liquidity_levels import get_liquidity_clusters
            liquidity_clusters = get_liquidity_clusters(symbol, ml.current_price)
        except Exception:
            pass

    if liquidity_clusters is not None:
        try:
            import dataclasses
            from app.domain.data.liquidity_levels import snap_to_liquidity
            new_tp, new_sl, snap_info = snap_to_liquidity(
                ml.direction, ml.current_price,
                ml.take_profit, ml.stop_loss,
                liquidity_clusters,
            )
            if snap_info:
                ml = dataclasses.replace(ml, take_profit=new_tp, stop_loss=new_sl)
        except Exception:
            pass

    # 4d. Apply event-day ATR multiplier AFTER direction is enforced
    if event_adj["atr_multiplier"] != 1.0:
        import dataclasses
        mult = event_adj["atr_multiplier"]
        kelly_mult = event_adj["kelly_reduction"]
        close = ml.current_price
        if ml.direction == "BUY":
            new_tp = close + (ml.take_profit - close) * mult
            new_sl = close - (close - ml.stop_loss) * mult
        else:
            new_tp = close - (close - ml.take_profit) * mult
            new_sl = close + (ml.stop_loss - close) * mult
        ml = dataclasses.replace(
            ml,
            take_profit=round(new_tp, 4),
            stop_loss=round(new_sl, 4),
            kelly_size=round(ml.kelly_size * kelly_mult, 2),
        )


    # 5. News
    news_items = get_news(symbol, limit=3)

    # 5b. Earnings warning
    earnings_flag = None
    try:
        from app.domain.data.earnings import get_earnings_flag
        earnings_flag = get_earnings_flag(symbol)
    except Exception:
        pass

    # 5c. Calendar suppression — check for high-impact macro events today
    macro_event_today = None
    try:
        from app.domain.data.calendar_data import fetch_calendar
        from datetime import datetime, timezone
        events = fetch_calendar()
        now = datetime.now(timezone.utc)
        for ev in events:
            if not ev.get("date") or ev.get("impact") != "High":
                continue
            try:
                ev_dt = datetime.fromisoformat(ev["date"])
                if ev_dt.tzinfo is None:
                    ev_dt = ev_dt.replace(tzinfo=timezone.utc)
                hours_away = (ev_dt - now).total_seconds() / 3600
                if -1 <= hours_away <= 24:
                    macro_event_today = {
                        "title": ev["title"],
                        "country": ev["country"],
                        "hours_away": round(hours_away, 1),
                    }
                    break
            except Exception:
                continue
    except Exception:
        pass
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
        confluence_score=score_label,
        session_info=session_info,
        volume_ratio=ml.volume_ratio,
        news=news_dicts,
        reasoning=reasoning,
        generated_at=datetime.now(timezone.utc).isoformat(),
        market_open=__import__('app.infrastructure.db.signal_history', fromlist=['is_open']).is_open(symbol),
        earnings_flag=earnings_flag,
        macro_event_today=macro_event_today,
        event_adjustment=event_adj if event_adj["event_type"] else None,
        data_warnings=data_warnings if data_warnings else None,
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
        from app.domain.core.energy_detector import compute_energy_state
        energy = compute_energy_state(df)
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

def validate_model_features(model, symbol: str) -> bool:
    """Returns True if model features match current FEATURE_COLUMNS."""
    from app.domain.ml.features import FEATURE_COLUMNS
    expected = set(FEATURE_COLUMNS)
    actual = set()
    if hasattr(model, "feature_names_"):
        actual = set(model.feature_names_)
    elif hasattr(model, "feature_names_in_"):
        actual = set(model.feature_names_in_)
    elif hasattr(model, "get_booster"):
        actual = set(model.get_booster().feature_names or [])
    if actual and actual != expected:
        import logging
        logging.getLogger(__name__).warning(
            f"[{symbol}] Feature mismatch — model has {len(actual)} features, "
            f"current pipeline has {len(expected)}. Retrain needed."
        )
        return False
    return True
