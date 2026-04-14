"""
Energy-Based Market State Detector.
Detects whether market energy is coiled (building), releasing (trending),
or exhausted (overextended). Used to gate and context-qualify signals.

States:
  coiled     → volatility compressed, breakout imminent, wait for confirmation
  releasing  → momentum active, trade with trend, best signal window
  exhausted  → move overextended, mean reversion likely, fade breakouts
"""
import logging
import numpy as np
import pandas as pd

log = logging.getLogger(__name__)


def _atr(high, low, close, period=14):
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low  - close.shift()).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def compute_energy_state(df: pd.DataFrame) -> dict:
    """
    Compute market energy state from OHLCV data.
    Requires at least 30 rows. Returns state dict.
    """
    try:
        if len(df) < 30:
            return {"state": "unknown", "score": 0.5, "reason": "insufficient_data"}

        close  = df["Close"].astype(float)
        high   = df["High"].astype(float)
        low    = df["Low"].astype(float)
        volume = df["Volume"].astype(float).replace(0, np.nan).fillna(1.0)

        # 1. Volatility compression: ATR(5) / ATR(20)
        # < 0.7 = compressed (coiled), > 1.3 = expanding (releasing)
        atr5  = _atr(high, low, close, 5)
        atr20 = _atr(high, low, close, 20)
        atr_ratio = float((atr5 / atr20.replace(0, np.nan)).iloc[-1])

        # 2. Bollinger band width: (upper - lower) / middle
        sma20  = close.rolling(20).mean()
        std20  = close.rolling(20).std()
        bb_width = float(((2 * std20) / sma20.replace(0, np.nan)).iloc[-1])
        bb_width_20d_avg = float(((2 * std20) / sma20.replace(0, np.nan)).rolling(20).mean().iloc[-1])
        bb_squeeze = bb_width / bb_width_20d_avg if bb_width_20d_avg > 0 else 1.0

        # 3. Volume confirmation: current vol vs 20d avg
        vol_ratio = float((volume / volume.rolling(20).mean()).iloc[-1])

        # 4. Price momentum acceleration: ROC(5) vs ROC(20)/4
        roc5  = float(close.pct_change(5).iloc[-1])
        roc20 = float(close.pct_change(20).iloc[-1])
        momentum_accel = roc5 - (roc20 / 4)

        # 5. Mean reversion z-score (how far from mean in ATR units)
        atr14 = _atr(high, low, close, 14)
        mean_rev_z = float(((close - sma20) / atr14.replace(0, np.nan)).iloc[-1])

        # --- Score computation ---
        # Each component scored 0-1, weighted
        compression_score = max(0, min(1, (1 - atr_ratio) / 0.6))   # low = coiled
        squeeze_score     = max(0, min(1, (1 - bb_squeeze) / 0.5))   # low = coiled
        momentum_score    = min(1, abs(momentum_accel) * 20)          # high = releasing
        volume_score      = min(1, (vol_ratio - 0.5) / 1.5)          # high = confirming
        extension_score   = min(1, abs(mean_rev_z) / 3.0)            # high = exhausted

        # Weighted energy score (0 = coiled, 1 = exhausted)
        coiled_signal    = (compression_score * 0.4 + squeeze_score * 0.3)
        releasing_signal = (momentum_score * 0.5 + volume_score * 0.3)
        exhausted_signal = extension_score

        # Direction bias from momentum
        direction_bias = "up" if momentum_accel > 0 else "down"

        # Determine state
        if coiled_signal > 0.5 and releasing_signal < 0.3:
            state = "coiled"
            score = round(coiled_signal, 3)
            reason = f"ATR ratio={atr_ratio:.2f}, BB squeeze={bb_squeeze:.2f}"
        elif exhausted_signal > 0.65 and releasing_signal < 0.4:
            state = "exhausted"
            score = round(exhausted_signal, 3)
            reason = f"Mean rev z={mean_rev_z:.2f}, extension score={exhausted_signal:.2f}"
        elif releasing_signal > 0.35:
            state = "releasing"
            score = round(releasing_signal, 3)
            reason = f"Momentum accel={momentum_accel:.4f}, vol ratio={vol_ratio:.2f}"
        else:
            state = "neutral"
            score = 0.5
            reason = "no dominant energy signal"

        return {
            "state":           state,
            "score":           score,
            "direction_bias":  direction_bias,
            "reason":          reason,
            "components": {
                "atr_ratio":        round(atr_ratio, 3),
                "bb_squeeze":       round(bb_squeeze, 3),
                "vol_ratio":        round(vol_ratio, 3),
                "momentum_accel":   round(momentum_accel, 5),
                "mean_rev_z":       round(mean_rev_z, 3),
                "extension_score":  round(exhausted_signal, 3),
            }
        }

    except Exception as e:
        log.warning(f"[energy_detector] failed: {e}")
        return {"state": "unknown", "score": 0.5, "reason": str(e)}


def energy_signal_modifier(energy: dict, direction: str) -> dict:
    """
    Returns a modifier for signal confidence based on energy state.
    Used to adjust probability before EV gate.
    """
    state = energy.get("state", "unknown")
    bias  = energy.get("direction_bias", "neutral")

    # Map direction to bias alignment
    aligned = (direction == "BUY" and bias == "up") or               (direction == "SELL" and bias == "down")

    if state == "releasing" and aligned:
        return {"boost": 1.15, "suppress": False,
                "reason": "releasing_aligned — momentum confirms signal"}
    elif state == "releasing" and not aligned:
        return {"boost": 0.85, "suppress": False,
                "reason": "releasing_misaligned — momentum opposes signal"}
    elif state == "coiled":
        return {"boost": 0.90, "suppress": False,
                "reason": "coiled — breakout imminent but direction unclear"}
    elif state == "exhausted" and not aligned:
        return {"boost": 0.70, "suppress": False,
                "reason": "exhausted_opposed — mean reversion risk"}
    elif state == "exhausted" and aligned:
        return {"boost": 1.05, "suppress": False,
                "reason": "exhausted_aligned — momentum may continue briefly"}
    else:
        return {"boost": 1.0, "suppress": False, "reason": "neutral_energy"}
