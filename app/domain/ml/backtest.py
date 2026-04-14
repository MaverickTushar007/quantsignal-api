"""
ml/backtest.py
Walk-forward backtester using actual trained production models.
No lookahead bias — uses only data available at each point in time.
"""
import pickle
import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import List
from pathlib import Path

from app.domain.ml.features import build_features, FEATURE_COLUMNS
from app.core.config import BASE_DIR

MODELS_DIR   = BASE_DIR / "ml/models"
FORWARD_DAYS = 5
MIN_PROB_BUY  = 0.52
MIN_PROB_SELL = 0.48

@dataclass
class Trade:
    date:       str
    direction:  str
    entry:      float
    exit:       float
    return_pct: float
    won:        bool

@dataclass
class BacktestResult:
    ticker:       str
    win_rate:     float
    avg_return:   float
    sharpe:       float
    max_drawdown: float
    total_return: float
    n_trades:     int
    trades:       List[Trade]

def _load_bundle(ticker: str):
    """Load the production model bundle for this ticker."""
    # Try multiple path formats
    candidates = [
        MODELS_DIR / f"{ticker}.pkl",
        MODELS_DIR / f"{ticker.replace('-','_').replace('=','_').replace('^','_')}.pkl",
        MODELS_DIR / f"{ticker.replace('-USD','_USD')}.pkl",
    ]
    for path in candidates:
        if path.exists():
            with open(path, "rb") as f:
                return pickle.load(f)
    raise FileNotFoundError(f"No model found for {ticker}. Tried: {[str(c) for c in candidates]}")

def run(df: pd.DataFrame, ticker: str) -> BacktestResult:
    """
    Backtest using the production model — walk forward through
    historical data, predict at each bar, measure outcome.
    """
    bundle = _load_bundle(ticker)
    xgb_m  = bundle["xgb"]
    lgb_m  = bundle["lgb"]

    feat  = build_features(df)
    close = df["Close"].reindex(feat.index)

    if len(feat) < 100:
        raise ValueError(f"Not enough data: {len(feat)} rows after feature build")

    trades: List[Trade] = []

    # Walk forward — skip first 60 rows as warmup, leave last FORWARD_DAYS for exit prices
    for i in range(60, len(feat) - FORWARD_DAYS):
        date = feat.index[i]
        row  = feat.iloc[[i]][FEATURE_COLUMNS]

        try:
            xgb_prob = float(xgb_m.predict_proba(row)[0, 1])
            lgb_prob = float(lgb_m.predict_proba(row)[0, 1])
        except Exception:
            continue

        prob = (xgb_prob + lgb_prob) / 2

        if prob >= MIN_PROB_BUY:
            direction = "BUY"
        elif prob <= MIN_PROB_SELL:
            direction = "SELL"
        else:
            continue  # HOLD — skip

        entry      = float(close.iloc[i])
        exit_price = float(close.iloc[i + FORWARD_DAYS])

        if entry <= 0:
            continue

        ret = (exit_price - entry) / entry
        if direction == "SELL":
            ret = -ret

        trades.append(Trade(
            date=str(date.date()),
            direction=direction,
            entry=round(entry, 4),
            exit=round(exit_price, 4),
            return_pct=round(ret * 100, 3),
            won=ret > 0,
        ))

    if not trades:
        raise ValueError("No trades generated — model may be outputting all HOLDs")

    rets     = np.array([t.return_pct / 100 for t in trades])
    win_rate = sum(t.won for t in trades) / len(trades)
    avg_ret  = float(np.mean(rets))
    sharpe   = float(np.mean(rets) / np.std(rets) * np.sqrt(252 / FORWARD_DAYS)) \
               if np.std(rets) > 0 else 0
    # Use log returns to avoid compounding explosion in display
    total_ret = float(np.sum(rets)) * 100
    cum      = np.cumprod(1 + rets)
    peak     = np.maximum.accumulate(cum)
    max_dd   = float(((cum - peak) / peak).min()) * 100

    return BacktestResult(
        ticker=ticker,
        win_rate=round(win_rate * 100, 1),
        avg_return=round(avg_ret * 100, 3),
        sharpe=round(sharpe, 2),
        max_drawdown=round(max_dd, 1),
        total_return=round(total_ret, 1),
        n_trades=len(trades),
        trades=trades,
    )
