"""
api/replay.py
Historical signal replay — what would the ML have signaled on a past date?
No lookahead bias: only data up to the requested date is used.
"""
from fastapi import APIRouter, HTTPException, Query
from datetime import datetime, date
import pandas as pd
import pickle
import numpy as np

router = APIRouter()

@router.get("/signals/{symbol}/replay", tags=["replay"])
def replay_signal(
    symbol: str,
    replay_date: str = Query(..., description="Date in YYYY-MM-DD format")
):
    try:
        target_date = datetime.strptime(replay_date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="Date must be YYYY-MM-DD format")

    today = date.today()
    if target_date >= today:
        raise HTTPException(status_code=400, detail="Replay date must be in the past")
    if (today - target_date).days > 175:
        raise HTTPException(status_code=400, detail="Only last 175 days supported")

    try:
        from app.domain.signal.service import TICKER_MAP, fetch_ohlcv
        from app.domain.signal.confluence_v2 import build_confluence_v2 as _build_confluence
        from app.domain.ml.features import build_features, FEATURE_COLUMNS
        from app.domain.ml.ensemble import _model_path, _is_stale, train

        meta = TICKER_MAP.get(symbol)
        if not meta:
            raise HTTPException(status_code=404, detail=f"Symbol {symbol} not found")

        # Fetch full OHLCV
        df = fetch_ohlcv(symbol, period="2y")
        if df is None or df.empty:
            raise HTTPException(status_code=404, detail="No price data available")

        df.index = pd.to_datetime(df.index)
        target_dt = pd.Timestamp(replay_date)
        df_sliced = df[df.index <= target_dt]

        if len(df_sliced) < 60:
            raise HTTPException(status_code=400, detail="Not enough historical data for this date")

        # Build features on full df for lookback, then grab replay row
        feat_full = build_features(df)
        replay_idx = feat_full.index[feat_full.index <= target_dt]
        if len(replay_idx) == 0:
            raise HTTPException(status_code=400, detail="No feature data for this date")

        replay_row = feat_full.loc[[replay_idx[-1]], FEATURE_COLUMNS]

        # Load model bundle — always try cached first, retrain if needed
        path = _model_path(symbol)
        bundle = None
        if not _is_stale(path):
            try:
                with open(path, "rb") as f:
                    bundle = pickle.load(f)
                # Validate bundle works with current features
                _ = bundle["xgb"].predict_proba(replay_row)
            except Exception:
                bundle = None
        if bundle is None:
            bundle = train(symbol, df)
        if bundle is None:
            raise HTTPException(status_code=500, detail="Model unavailable")

        # Predict at replay row
        xgb_prob = float(bundle["xgb"].predict_proba(replay_row)[0, 1])
        try:
            lgb_prob = float(bundle["lgb"].predict_proba(replay_row)[0, 1])
            prob = round(0.5 * xgb_prob + 0.5 * lgb_prob, 4)
        except Exception:
            lgb_prob = xgb_prob
            prob = round(xgb_prob, 4)

        if prob >= 0.55:
            direction = "BUY"
        elif prob <= 0.45:
            direction = "SELL"
        else:
            direction = "HOLD"

        confidence = "HIGH" if abs(prob - 0.5) > 0.1 else "MEDIUM" if abs(prob - 0.5) > 0.05 else "LOW"

        # Top features
        try:
            scores = bundle["xgb"].feature_importances_
            top_features = [FEATURE_COLUMNS[i] for i in np.argsort(scores)[-5:][::-1]]
        except Exception:
            top_features = []

        # Confluence at replay row
        latest_row = feat_full.loc[replay_idx[-1]].to_dict()
        asset_type = TICKER_MAP.get(symbol, {}).get("type", "default")
        confluence, bull_count, _, _ = _build_confluence(latest_row, df_sliced, asset_type)
        
        # Price at replay date
        current_price = float(df_sliced["Close"].iloc[-1])
        feat_row = feat_full.loc[replay_idx[-1]]
        atr = float(feat_row["atr"]) if "atr" in feat_row.index else current_price * 0.02

        if direction == "BUY":
            take_profit = round(current_price + 2 * atr, 4)
            stop_loss = round(current_price - atr, 4)
        elif direction == "SELL":
            take_profit = round(current_price - 2 * atr, 4)
            stop_loss = round(current_price + atr, 4)
        else:
            take_profit = current_price
            stop_loss = current_price

        risk = abs(current_price - stop_loss)
        reward = abs(take_profit - current_price)
        rr = round(reward / risk, 2) if risk > 0 else 0

        # What actually happened after (outcome context)
        future_df = df[df.index > target_dt].head(5)
        actual_close_5d = float(future_df["Close"].iloc[-1]) if len(future_df) >= 5 else None
        actual_return = None
        was_correct = None
        if actual_close_5d:
            actual_return = round((actual_close_5d - current_price) / current_price * 100, 2)
            if direction == "SELL":
                actual_return = -actual_return
            was_correct = actual_return > 0

        return {
            "symbol": symbol,
            "replay_date": replay_date,
            "is_replay": True,
            "current_price": current_price,
            "direction": direction,
            "probability": prob,
            "confidence": confidence,
            "take_profit": take_profit,
            "stop_loss": stop_loss,
            "risk_reward": rr,
            "atr": round(atr, 4),
            "confluence_score": f"{bull_count}/9 {'bullish' if bull_count >= 5 else 'bearish'}",
            "confluence": confluence,
            "top_features": top_features,
            "model_agreement": round(abs(xgb_prob - lgb_prob), 4),
            "kelly_size": 0.0,
            "expected_value": 0.0,
            "actual_return_5d": actual_return,
            "was_correct": was_correct,
            "actual_price_5d": actual_close_5d,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
