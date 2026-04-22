"""
app/domain/core/regime_detector.py
Classifies current volatility regime using ATR percentile.
LOW: ATR <= 25th percentile of 90-day history
HIGH: ATR >= 75th percentile
MEDIUM: everything else
"""
from __future__ import annotations
import logging
import pandas as pd

log = logging.getLogger(__name__)


def detect_regime(df: pd.DataFrame, current_atr: float) -> dict:
    """
    Classify volatility regime from OHLCV dataframe + current ATR.

    Args:
        df: OHLCV dataframe with High/Low columns, at least 60 bars
        current_atr: current ATR(14) value from ML ensemble

    Returns:
        dict with regime, percentile, modifier
    """
    try:
        if len(df) < 30 or current_atr <= 0:
            return {"regime": "MEDIUM", "percentile": 50.0, "modifier": 1.0}

        # Compute rolling ATR(14) across full history
        atr_series = (df["High"] - df["Low"]).rolling(14).mean().dropna()

        if len(atr_series) < 10:
            return {"regime": "MEDIUM", "percentile": 50.0, "modifier": 1.0}

        percentile = float((atr_series < current_atr).sum() / len(atr_series) * 100)

        if percentile <= 25.0:
            regime = "LOW"
            # Low volatility: trend signals are weaker, mean reversion stronger
            modifier = 0.92
        elif percentile >= 75.0:
            regime = "HIGH"
            # High volatility: breakout signals are noisier, reduce confidence
            modifier = 0.90
        else:
            regime = "MEDIUM"
            modifier = 1.0

        log.debug(
            f"[Regime] ATR={current_atr:.4f} percentile={percentile:.1f}% "
            f"regime={regime} modifier={modifier}"
        )

        return {
            "regime":     regime,
            "percentile": round(percentile, 1),
            "modifier":   modifier,
        }

    except Exception as e:
        log.warning(f"[Regime] detection failed: {e}")
        return {"regime": "MEDIUM", "percentile": 50.0, "modifier": 1.0}
