"""
Walk-forward backtest — zero lookahead.
Retrains model on train slice only, predicts on each test bar.
"""
import numpy as np
import pandas as pd
import pickle
import warnings
warnings.filterwarnings("ignore")

from dataclasses import dataclass, field
from typing import List, Dict

from app.domain.ml.features import build_features, FEATURE_COLUMNS
from app.domain.ml.ensemble import train

FORWARD_DAYS = 5
MIN_EV       = 0.015
SLIP_BPS     = {"crypto": 10, "nse": 8, "us": 5, "forex": 2}

def _asset_class(ticker):
    if ticker.endswith("-USD"): return "crypto"
    if ticker.endswith(".NS"):  return "nse"
    if ticker.endswith("=X"):   return "forex"
    return "us"

def _slip(ticker):
    return SLIP_BPS.get(_asset_class(ticker), 7) / 10000

def _regime(close, i):
    w = close.iloc[max(0, i-200):i+1]
    if len(w) < 50: return "unknown"
    sma50  = w.rolling(50).mean().iloc[-1]
    sma200 = w.rolling(200).mean().iloc[-1] if len(w) >= 200 else sma50
    price  = w.iloc[-1]
    ret20  = (w.iloc[-1]/w.iloc[-20]-1) if len(w) >= 20 else 0
    if price > sma50 > sma200 and ret20 > 0.02:  return "bull"
    if price < sma50 < sma200 and ret20 < -0.02: return "bear"
    return "ranging"

@dataclass
class Trade:
    date: str
    direction: str
    entry: float
    exit_price: float
    net_ret: float
    won: bool
    regime: str
    ev: float

@dataclass
class WFResult:
    ticker: str
    n_trades: int
    win_rate: float
    sharpe: float
    total_return: float
    max_drawdown: float
    cost_drag: float
    regime_summary: Dict
    trades: List[Trade] = field(default_factory=list)

def run(df: pd.DataFrame, ticker: str) -> WFResult:
    slip   = _slip(ticker)
    close  = df["Close"].astype(float)
    n      = len(df)

    # Walk-forward: train on first 60%, test on remaining 40%
    # The model is trained ONCE on the train slice — no future data
    train_end = int(n * 0.60)

    print(f"  Training on rows 0-{train_end} ({df.index[0].date()} → {df.index[train_end].date()})")
    print(f"  Testing  on rows {train_end}-{n} ({df.index[train_end].date()} → {df.index[-1].date()})")

    train_df = df.iloc[:train_end].copy()
    bundle   = train(ticker + "_WF", train_df)   # saves with _WF suffix so it doesn't overwrite production
    if bundle is None:
        raise ValueError(f"Training failed on {len(train_df)} rows")

    xgb_m = bundle["xgb"]
    lgb_m = bundle["lgb"]

    # Build features on full df (features use only past data — no leak)
    feat = build_features(df)
    atr  = (df["High"] - df["Low"]).rolling(14).mean().reindex(feat.index)

    trades: List[Trade] = []
    last_bar   = -999
    equity     = 1.0
    peak_eq    = 1.0
    circuit    = False
    gross_rets = []

    for i in range(train_end, n - FORWARD_DAYS):
        if i - last_bar < 2: continue
        if circuit:
            if equity / peak_eq > 0.92: circuit = False
            else: continue

        idx = feat.index.get_indexer([df.index[i]], method="nearest")[0]
        if idx < 0: continue

        row = feat.iloc[[idx]][FEATURE_COLUMNS]
        try:
            xp = float(xgb_m.predict_proba(row)[0, 1])
            lp = float(lgb_m.predict_proba(row)[0, 1])
        except Exception:
            continue

        prob = (xp + lp) / 2.0

        if prob >= 0.52:   direction = "BUY"
        elif prob <= 0.48: direction = "SELL"
        else:              continue

        entry = float(close.iloc[i])
        if entry <= 0: continue

        atr_val = float(atr.iloc[idx]) if idx < len(atr) else entry * 0.01

        # Dynamic ATR-based SL/TP — adapts to each symbol's volatility
        # SL = 1.0x ATR, TP = 1.8x ATR  → 1.8:1 R:R
        atr_pct = atr_val / entry if entry > 0 else 0.015
        atr_pct = max(0.008, min(atr_pct, 0.04))  # clamp: 0.8% – 4%
        sl_pct  = 1.0 * atr_pct
        tp_pct  = 1.8 * atr_pct
        gross   = None

        for fwd in range(1, FORWARD_DAYS + 1):
            bar_price = float(close.iloc[i + fwd])
            bar_ret   = (bar_price - entry) / entry
            if direction == "SELL": bar_ret = -bar_ret

            if bar_ret <= -sl_pct:   # stop hit
                gross = -sl_pct
                break
            if bar_ret >= tp_pct:    # target hit
                gross = tp_pct
                break

        if gross is None:   # held to expiry
            exit_price = float(close.iloc[i + FORWARD_DAYS])
            gross = (exit_price - entry) / entry
            if direction == "SELL": gross = -gross

        atr_val = float(atr.iloc[idx]) if idx < len(atr) else entry * 0.01

        # Regime-conditional direction filter:
        # In bull regime  → only BUY signals (don't fight the trend)
        # In bear regime  → only SELL signals
        # In ranging      → allow both directions
        cur_regime = _regime(close, i)
        if cur_regime == "bull"  and direction == "SELL": continue
        if cur_regime == "bear"  and direction == "BUY":  continue

        # EV filter: use prob-based kelly fraction instead of ATR ratio
        # This is more robust for ranging markets with variable ATR
        edge = prob if direction == "BUY" else 1 - prob
        # Simplified EV: edge must exceed 0.53 (just above random)
        # OR ATR-based EV must be positive
        tp_dist = 1.5 * atr_val
        sl_dist = 1.0 * atr_val
        ev_atr  = ((edge * tp_dist) - ((1 - edge) * sl_dist)) / entry if entry > 0 else 0
        # Pass if either: strong prob edge OR positive ATR EV
        if edge < 0.53 and ev_atr < 0.005: continue
        ev = ev_atr

        net = gross - 2 * slip
        gross_rets.append(gross)

        trades.append(Trade(
            date=str(df.index[i].date()),
            direction=direction,
            entry=round(entry, 4),
            exit_price=round(entry * (1 + (gross if direction=='BUY' else -gross)), 4),
            net_ret=round(net * 100, 3),
            won=net > 0,
            regime=_regime(close, i),
            ev=round(ev, 4),
        ))

        last_bar = i
        equity  *= (1 + net)
        if equity > peak_eq: peak_eq = equity
        if equity / peak_eq < 0.80: circuit = True

    if not trades:
        raise ValueError("No trades generated in test window")

    rets     = np.array([t.net_ret / 100 for t in trades])
    win_rate = sum(t.won for t in trades) / len(trades) * 100
    sharpe   = float(np.mean(rets) / np.std(rets) * np.sqrt(252 / FORWARD_DAYS)) if np.std(rets) > 0 else 0
    tot_ret  = float(np.sum(rets) * 100)
    cum      = np.cumprod(1 + rets)
    peak     = np.maximum.accumulate(cum)
    max_dd   = float(((cum - peak) / peak).min() * 100)
    cost_drag = float(np.sum(gross_rets) * 100) - tot_ret

    reg_summary = {}
    for reg in ["bull", "bear", "ranging", "unknown"]:
        rt = [t for t in trades if t.regime == reg]
        if rt:
            reg_summary[reg] = {
                "n": len(rt),
                "win_rate": round(sum(t.won for t in rt) / len(rt) * 100, 1),
                "avg_return": round(float(np.mean([t.net_ret for t in rt])), 3),
            }

    return WFResult(
        ticker=ticker,
        n_trades=len(trades),
        win_rate=round(win_rate, 1),
        sharpe=round(sharpe, 2),
        total_return=round(tot_ret, 1),
        max_drawdown=round(max_dd, 1),
        cost_drag=round(cost_drag, 2),
        regime_summary=reg_summary,
        trades=trades,
    )
