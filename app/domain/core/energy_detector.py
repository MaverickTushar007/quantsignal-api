"""
Energy-Based Market State Detector.
Measures whether market energy is coiled (building), releasing (active), or exhausted.
Coiled  → breakout imminent, wait for confirmation
Releasing → momentum active, best signal window
Exhausted → move nearly over, caution / mean reversion likely
"""
import logging
import numpy as np
log = logging.getLogger(__name__)

def compute_market_energy(df) -> dict:
    """
    Takes an OHLCV DataFrame (daily), returns energy state dict.
    Requires at least 30 rows.
    """
    try:
        if df is None or len(df) < 30:
            return _unknown("insufficient_data")

        close  = df["Close"].values.astype(float)
        high   = df["High"].values.astype(float)
        low    = df["Low"].values.astype(float)
        volume = df["Volume"].values.astype(float)

        # ── 1. ATR compression ─────────────────────────────────────────
        tr = np.maximum(high - low,
             np.maximum(abs(high - np.roll(close, 1)),
                        abs(low  - np.roll(close, 1))))
        tr[0] = high[0] - low[0]
        atr_5  = float(np.mean(tr[-5:]))
        atr_20 = float(np.mean(tr[-20:]))
        atr_ratio = atr_5 / atr_20 if atr_20 > 0 else 1.0
        # < 0.7 = compressed (coiled), > 1.3 = expanding (releasing)

        # ── 2. Bollinger Band width ────────────────────────────────────
        sma_20  = float(np.mean(close[-20:]))
        std_20  = float(np.std(close[-20:]))
        bb_width = (2 * std_20) / sma_20 if sma_20 > 0 else 0.02
        bb_avg   = float(np.mean([
            (2 * np.std(close[i-20:i])) / np.mean(close[i-20:i])
            for i in range(20, min(len(close), 60))
            if np.mean(close[i-20:i]) > 0
        ])) if len(close) >= 40 else bb_width
        bb_squeeze = bb_width / bb_avg if bb_avg > 0 else 1.0
        # < 0.7 = squeeze (coiled)

        # ── 3. Volume divergence ───────────────────────────────────────
        vol_5  = float(np.mean(volume[-5:]))
        vol_20 = float(np.mean(volume[-20:]))
        vol_ratio = vol_5 / vol_20 if vol_20 > 0 else 1.0
        # < 0.7 = low volume (accumulation/coil), > 1.5 = breakout volume

        # ── 4. Momentum acceleration ───────────────────────────────────
        roc_5  = (close[-1] - close[-6])  / close[-6]  if close[-6]  > 0 else 0
        roc_20 = (close[-1] - close[-21]) / close[-21] if close[-21] > 0 else 0
        momentum_accel = roc_5 - (roc_20 / 4)
        # Positive = accelerating up, negative = accelerating down

        # ── 5. Compute composite energy score ─────────────────────────
        # Score 0-1: higher = more energy releasing
        compression_score = 1 - min(atr_ratio, 2.0) / 2.0       # low ATR = high compression
        squeeze_score     = 1 - min(bb_squeeze, 2.0) / 2.0       # tight BB = high compression
        volume_score      = min(vol_ratio, 3.0) / 3.0             # high volume = releasing
        momentum_score    = min(abs(momentum_accel) * 20, 1.0)    # strong momentum = releasing

        # Weighted composite
        energy_score = (
            compression_score * 0.30 +
            squeeze_score     * 0.25 +
            volume_score      * 0.25 +
            momentum_score    * 0.20
        )

        # ── 6. Determine state ─────────────────────────────────────────
        if atr_ratio < 0.75 and bb_squeeze < 0.75:
            state = "coiled"        # ATR AND BB both compressed → spring loaded
        elif atr_ratio > 1.2 or vol_ratio > 1.4:
            state = "releasing"     # expanding volatility or high volume → breakout
        elif momentum_score > 0.6 and vol_ratio < 0.8:
            state = "exhausted"     # strong move on declining volume → fading
        elif energy_score < 0.35:
            state = "coiled"
        elif energy_score > 0.60:
            state = "releasing"
        else:
            state = "neutral"

        # Direction bias
        direction_bias = "up" if momentum_accel > 0.001 else "down" if momentum_accel < -0.001 else "neutral"

        # Trading implication
        implications = {
            "coiled":    "Breakout imminent — wait for volume confirmation before entering",
            "releasing": "Momentum active — best signal window, trade with trend",
            "exhausted": "Move likely ending — caution on new entries, watch for reversal",
            "neutral":   "No strong energy signal — standard signal filters apply",
        }

        return {
            "state":           state,
            "score":           round(energy_score, 3),
            "direction_bias":  direction_bias,
            "implication":     implications[state],
            "components": {
                "atr_ratio":       round(atr_ratio, 3),
                "bb_squeeze":      round(bb_squeeze, 3),
                "volume_ratio":    round(vol_ratio, 3),
                "momentum_accel":  round(momentum_accel, 4),
            }
        }

    except Exception as e:
        log.warning(f"[energy_detector] failed: {e}")
        return _unknown(str(e))


def _unknown(reason: str) -> dict:
    return {
        "state":          "unknown",
        "score":          0.5,
        "direction_bias": "neutral",
        "implication":    "Energy state unavailable",
        "error":          reason,
    }
