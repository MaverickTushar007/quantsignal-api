from __future__ import annotations
from typing import Optional
import numpy as np

def compute_vwap(highs, lows, closes, volumes, session_bars=1440) -> np.ndarray:
    h = np.array(highs,   dtype=float)
    l = np.array(lows,    dtype=float)
    c = np.array(closes,  dtype=float)
    v = np.array(volumes, dtype=float)
    typical = (h + l + c) / 3.0
    tpv  = typical * v
    vwap = np.zeros(len(c))
    for i in range(len(c)):
        session_start = (i // session_bars) * session_bars
        cum_tpv = tpv[session_start:i+1].sum()
        cum_vol = v[session_start:i+1].sum()
        vwap[i] = cum_tpv / cum_vol if cum_vol > 0 else typical[i]
    return vwap

def vwap_features(highs, lows, closes, volumes, slope_window=10, session_bars=1440) -> dict:
    if len(closes) < slope_window + 1:
        return {"dist_vwap": 0.0, "vwap_slope": 0.0, "vwap_signal": 0}
    vwap = compute_vwap(highs, lows, closes, volumes, session_bars)
    current_close = closes[-1]
    current_vwap  = vwap[-1]
    if current_vwap == 0:
        return {"dist_vwap": 0.0, "vwap_slope": 0.0, "vwap_signal": 0}
    dist_vwap   = (current_close - current_vwap) / current_vwap
    vwap_recent = vwap[-slope_window:]
    vwap_slope  = (vwap_recent[-1] - vwap_recent[0]) / vwap_recent[0] if len(vwap_recent) >= 2 and vwap_recent[0] != 0 else 0.0
    NOISE_BAND = 0.0015
    if abs(dist_vwap) < NOISE_BAND:
        vwap_signal = 0
    elif dist_vwap > 0 and vwap_slope >= 0:
        vwap_signal = 1
    elif dist_vwap < 0 and vwap_slope <= 0:
        vwap_signal = -1
    else:
        vwap_signal = 0
    return {
        "dist_vwap":   round(float(dist_vwap),  6),
        "vwap_slope":  round(float(vwap_slope), 6),
        "vwap_signal": int(vwap_signal),
    }

def vwap_confluence_score(vwap_signal: int, direction: str) -> float:
    if direction == "BUY"  and vwap_signal == 1:  return 1.0
    if direction == "SELL" and vwap_signal == -1: return 1.0
    return 0.0

def vwap_execution_benchmark(executed_prices, executed_volumes, highs, lows, closes, market_volumes, session_bars=1440) -> dict:
    if not executed_prices or not executed_volumes:
        return {}
    exec_vwap   = sum(p*v for p,v in zip(executed_prices, executed_volumes)) / sum(executed_volumes)
    market_vwap = float(compute_vwap(highs, lows, closes, market_volumes, session_bars)[-1])
    slippage_bps = (exec_vwap - market_vwap) / market_vwap * 10_000
    return {
        "execution_vwap": round(exec_vwap, 6),
        "market_vwap":    round(market_vwap, 6),
        "slippage_bps":   round(slippage_bps, 2),
        "beat_vwap":      exec_vwap <= market_vwap,
    }
