"""
ml/backtest.py
Walk-forward backtester — no lookahead bias.
Produces the exact metrics shown in your dashboard:
win rate, Sharpe, max drawdown, avg return, total return.
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import List

from ml.features import build_features, FEATURE_COLUMNS

TRAIN_WINDOW  = 252   # 1 year training
TEST_WINDOW   = 63    # 3 months testing per fold
FORWARD_DAYS  = 5
RETURN_THRESH = 0.02


@dataclass
class Trade:
    date:        str
    direction:   str
    entry:       float
    exit:        float
    return_pct:  float
    won:         bool


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


def run(df: pd.DataFrame, ticker: str) -> BacktestResult:
    import xgboost as xgb
    import lightgbm as lgb
    from sklearn.calibration import CalibratedClassifierCV

    feat  = build_features(df)
    close = df["Close"].reindex(feat.index)
    n     = len(feat)

    if n < TRAIN_WINDOW + TEST_WINDOW:
        raise ValueError(f"Not enough data: {n} rows")

    trades: List[Trade] = []
    start = 0

    while start + TRAIN_WINDOW + TEST_WINDOW <= n:
        tr_idx   = feat.index[start : start + TRAIN_WINDOW]
        te_idx   = feat.index[start + TRAIN_WINDOW : start + TRAIN_WINDOW + TEST_WINDOW]

        X_tr     = feat.loc[tr_idx, FEATURE_COLUMNS].values
        fut      = close.pct_change(FORWARD_DAYS).shift(-FORWARD_DAYS).loc[tr_idx]
        y_tr_raw = np.where(fut > RETURN_THRESH, 1,
                   np.where(fut < -RETURN_THRESH, 0, np.nan))
        valid    = ~np.isnan(y_tr_raw)

        if valid.sum() < 50:
            start += TEST_WINDOW
            continue

        X_tr_v = X_tr[valid]
        y_tr_v = y_tr_raw[valid].astype(int)

        xgb_m = CalibratedClassifierCV(
            xgb.XGBClassifier(n_estimators=100, max_depth=4,
                              learning_rate=0.05, random_state=42,
                              eval_metric="logloss", verbosity=0),
            cv=3, method="isotonic")
        lgb_m = CalibratedClassifierCV(
            lgb.LGBMClassifier(n_estimators=100, max_depth=4,
                               learning_rate=0.05, random_state=42,
                               verbose=-1),
            cv=3, method="isotonic")
        xgb_m.fit(X_tr_v, y_tr_v)
        lgb_m.fit(X_tr_v, y_tr_v)

        for date in te_idx[:-FORWARD_DAYS]:
            row    = feat.loc[date, FEATURE_COLUMNS].values.reshape(1, -1)
            row_df = pd.DataFrame(row, columns=FEATURE_COLUMNS)
            prob   = (float(xgb_m.predict_proba(row)[:,1]) +
                      float(lgb_m.predict_proba(row_df)[:,1])) / 2

            if prob >= 0.55:
                direction = "BUY"
            elif prob <= 0.45:
                direction = "SELL"
            else:
                continue

            future_dates = close.index[close.index > date]
            if len(future_dates) < FORWARD_DAYS:
                continue

            entry      = float(close.loc[date])
            exit_price = float(close.iloc[close.index.get_loc(date) + FORWARD_DAYS])
            ret        = (exit_price - entry) / entry
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

        start += TEST_WINDOW

    if not trades:
        raise ValueError("No trades generated")

    rets         = np.array([t.return_pct / 100 for t in trades])
    win_rate     = sum(t.won for t in trades) / len(trades)
    avg_ret      = float(np.mean(rets))
    sharpe       = float(np.mean(rets) / np.std(rets) * np.sqrt(252 / FORWARD_DAYS)) if np.std(rets) > 0 else 0
    total_ret    = float(np.prod(1 + rets) - 1) * 100
    cum          = np.cumprod(1 + rets)
    peak         = np.maximum.accumulate(cum)
    max_dd       = float(((cum - peak) / peak).min()) * 100

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
