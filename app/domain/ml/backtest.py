"""
ml/backtest.py
Walk-forward backtester — no lookahead, includes transaction costs, regime split.
"""
import pickle
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import List, Dict
from pathlib import Path

from app.domain.ml.features import build_features, FEATURE_COLUMNS
from app.core.config import BASE_DIR

MODELS_DIR   = BASE_DIR / "ml/models"
FORWARD_DAYS = 5
MIN_PROB_BUY  = 0.52
MIN_PROB_SELL = 0.48
MIN_EV        = 0.015   # reject signals below this EV — reduces noise trades

# Slippage per side in bps by asset class
SLIPPAGE_BPS = {
    "crypto": 10,
    "nse":    8,
    "us":     5,
    "forex":  2,
    "default": 7,
}

CRYPTO_TICKERS = {
    "BTC-USD","ETH-USD","SOL-USD","BNB-USD","XRP-USD","DOGE-USD",
    "ADA-USD","AVAX-USD","MATIC-USD","DOT-USD","LINK-USD","LTC-USD",
    "ATOM-USD","NEAR-USD","OP-USD","INJ-USD","FET-USD","PEPE-USD"
}

def _asset_class(ticker: str) -> str:
    if ticker in CRYPTO_TICKERS:       return "crypto"
    if ticker.endswith(".NS"):         return "nse"
    if ticker.endswith("=X"):          return "forex"
    return "us"

def _slippage(ticker: str) -> float:
    return SLIPPAGE_BPS.get(_asset_class(ticker), 7) / 10000

def _bar_regime(close_series: pd.Series, i: int) -> str:
    """Determine regime at bar i using only data up to i."""
    window = close_series.iloc[max(0, i-200):i+1]
    if len(window) < 50:
        return "unknown"
    sma50  = window.rolling(50).mean().iloc[-1]
    sma200 = window.rolling(200).mean().iloc[-1] if len(window) >= 200 else sma50
    price  = window.iloc[-1]
    ret20  = (window.iloc[-1] - window.iloc[-20]) / window.iloc[-20] if len(window) >= 20 else 0
    if price > sma50 > sma200 and ret20 > 0.02:
        return "bull"
    elif price < sma50 < sma200 and ret20 < -0.02:
        return "bear"
    return "ranging"

@dataclass
class Trade:
    date:       str
    direction:  str
    entry:      float
    exit:       float
    return_pct: float
    won:        bool
    regime:     str = "unknown"
    ev:         float = 0.0

@dataclass
class BacktestResult:
    ticker:          str
    win_rate:        float
    avg_return:      float
    sharpe:          float
    max_drawdown:    float
    total_return:    float
    n_trades:        int
    trades:          List[Trade]
    regime_summary:  Dict = field(default_factory=dict)
    cost_drag_pct:   float = 0.0

def _load_bundle(ticker: str):
    candidates = [
        MODELS_DIR / f"{ticker}.pkl",
        MODELS_DIR / f"{ticker.replace('-','_').replace('=','_').replace('^','_')}.pkl",
    ]
    for path in candidates:
        if path.exists():
            with open(path, "rb") as f:
                return pickle.load(f)
    raise FileNotFoundError(f"No model found for {ticker}. Tried: {[str(c) for c in candidates]}")

def run(df: pd.DataFrame, ticker: str) -> BacktestResult:
    bundle = _load_bundle(ticker)
    xgb_m  = bundle["xgb"]
    lgb_m  = bundle["lgb"]

    feat  = build_features(df)
    close = df["Close"].reindex(feat.index)
    slip  = _slippage(ticker)

    if len(feat) < 100:
        raise ValueError(f"Not enough data: {len(feat)} rows")

    # Walk-forward: train window = first 60% of data, test = remaining 40%
    # Within test window, walk bar by bar (no future info)
    n = len(feat)
    test_start = int(n * 0.60)  # start testing after 60% warmup

    trades: List[Trade] = []
    gross_returns = []

    last_trade_bar = -999  # cooldown tracker
    peak_equity    = 1.0
    equity         = 1.0
    circuit_open   = False  # drawdown circuit breaker

    for i in range(max(60, test_start), n - FORWARD_DAYS):
        # Cooldown: min 2 bars between trades (reduces overtrading)
        if i - last_trade_bar < 2:
            continue
        # Circuit breaker: stop trading if drawdown > 20%
        if circuit_open:
            if equity / peak_equity > 0.92:   # resume after partial recovery
                circuit_open = False
            else:
                continue

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
            continue

        entry      = float(close.iloc[i])
        exit_price = float(close.iloc[i + FORWARD_DAYS])
        if entry <= 0:
            continue

        # Gross return
        gross_ret = (exit_price - entry) / entry
        if direction == "SELL":
            gross_ret = -gross_ret

        # EV filter — skip low-conviction trades
        atr = float((df["High"] - df["Low"]).rolling(14).mean().reindex(feat.index).iloc[i])
        tp_dist = 2.0 * atr
        sl_dist = 1.0 * atr
        edge = prob if direction == "BUY" else 1 - prob
        ev = ((edge * tp_dist) - ((1 - edge) * sl_dist)) / entry if entry > 0 else 0
        if ev < MIN_EV:
            continue

        # Net return after round-trip slippage
        net_ret = gross_ret - 2 * slip
        gross_returns.append(gross_ret)

        regime = _bar_regime(close, i)

        trades.append(Trade(
            date=str(date.date()),
            direction=direction,
            entry=round(entry, 4),
            exit=round(exit_price, 4),
            return_pct=round(net_ret * 100, 3),
            won=net_ret > 0,
            regime=regime,
            ev=round(ev, 4),
        ))
        last_trade_bar = i
        equity *= (1 + net_ret)
        if equity > peak_equity:
            peak_equity = equity
        if equity / peak_equity < 0.80:   # 20% drawdown triggers circuit breaker
            circuit_open = True

    if not trades:
        raise ValueError("No trades generated")

    rets     = np.array([t.return_pct / 100 for t in trades])
    win_rate = sum(t.won for t in trades) / len(trades)
    avg_ret  = float(np.mean(rets))
    sharpe   = float(np.mean(rets) / np.std(rets) * np.sqrt(252 / FORWARD_DAYS)) \
               if np.std(rets) > 0 else 0
    total_ret = float(np.sum(rets)) * 100
    cum      = np.cumprod(1 + rets)
    peak     = np.maximum.accumulate(cum)
    max_dd   = float(((cum - peak) / peak).min()) * 100

    # Regime breakdown
    regime_summary = {}
    for reg in ["bull", "bear", "ranging", "unknown"]:
        reg_trades = [t for t in trades if t.regime == reg]
        if reg_trades:
            reg_rets = [t.return_pct for t in reg_trades]
            regime_summary[reg] = {
                "n": len(reg_trades),
                "win_rate": round(sum(t.won for t in reg_trades) / len(reg_trades) * 100, 1),
                "avg_return": round(float(np.mean(reg_rets)), 3),
            }

    # Cost drag
    gross_arr = np.array(gross_returns)
    cost_drag = float(np.sum(gross_arr) * 100) - total_ret if gross_returns else 0

    return BacktestResult(
        ticker=ticker,
        win_rate=round(win_rate * 100, 1),
        avg_return=round(avg_ret * 100, 3),
        sharpe=round(sharpe, 2),
        max_drawdown=round(max_dd, 1),
        total_return=round(total_ret, 1),
        n_trades=len(trades),
        trades=trades,
        regime_summary=regime_summary,
        cost_drag_pct=round(cost_drag, 2),
    )
