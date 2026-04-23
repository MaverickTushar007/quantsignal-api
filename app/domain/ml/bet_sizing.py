"""
app/domain/ml/bet_sizing.py

Kelly-based position sizing from signal confidence for QuantSignal.
Replaces fixed BUY/SELL/HOLD output with a continuous position size in [-1, 1].

Theory (AFML Chapter 10):
  - Kelly criterion: f* = (p * b - q) / b  where b = odds, p = win prob, q = 1-p
  - For binary bets with equal payoff (b=1): f* = 2p - 1  (this is the "bet fraction")
  - Fractional Kelly (safer): scale f* by fraction (e.g. 0.5x Kelly = half sizing)
  - Meta-label confidence filters out low-conviction trades entirely

Usage:
    from app.domain.ml.bet_sizing import compute_bet_size, BetSizer

    # Single signal
    size = compute_bet_size(probability=0.72, method="fractional_kelly", kelly_fraction=0.5)
    # Returns: 0.22  (i.e. 22% of capital, long)

    # Batch (production use)
    sizer = BetSizer(kelly_fraction=0.5, min_confidence=0.55, max_position=1.0)
    signal_out = sizer.size_signal({"direction": "BUY", "probability": 0.72})
    # Returns: {"direction": "BUY", "probability": 0.72, "position_size": 0.22, "kelly_raw": 0.44}
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Dict, Optional

import numpy as np

logger = logging.getLogger(__name__)

# ── constants ────────────────────────────────────────────────────────────────

DEFAULT_KELLY_FRACTION = 0.5   # half-Kelly is standard practice (avoids ruin)
DEFAULT_MIN_CONFIDENCE  = 0.55  # signals below this are sized to 0
DEFAULT_MAX_POSITION    = 1.0   # cap at 100% of capital


# ── core math ─────────────────────────────────────────────────────────────────

def kelly_fraction(p: float, b: float = 1.0) -> float:
    """
    Full Kelly criterion for a binary bet.

    Args:
        p:  win probability  (0 < p < 1)
        b:  net odds — profit per unit wagered (default 1.0 = even money)

    Returns:
        f*: fraction of capital to bet (negative = short)
        Range: [-1, 1]
    """
    p = float(np.clip(p, 1e-6, 1 - 1e-6))
    q = 1.0 - p
    f = (p * b - q) / b
    return float(np.clip(f, -1.0, 1.0))


def compute_bet_size(
    probability: float,
    direction: str = "BUY",
    method: str = "fractional_kelly",
    kelly_fraction_scale: float = DEFAULT_KELLY_FRACTION,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    max_position: float = DEFAULT_MAX_POSITION,
    odds: float = 1.0,
) -> float:
    """
    Convert signal probability → position size in [-1, 1].

    Args:
        probability:         raw model probability (0-1) for the predicted direction
        direction:           "BUY", "SELL", or "HOLD"
        method:              "fractional_kelly" | "sigmoid" | "linear"
        kelly_fraction_scale: fraction of full Kelly to use (0.5 = half-Kelly)
        min_confidence:      probabilities below this map to size=0
        max_position:        hard cap on position size
        odds:                net odds for Kelly calc (1.0 = even money)

    Returns:
        position_size in [-max_position, max_position]
        Positive = long, negative = short, 0 = no trade
    """
    if direction == "HOLD":
        return 0.0

    p = float(probability)

    # below minimum confidence threshold → no trade
    if p < min_confidence:
        return 0.0

    if method == "fractional_kelly":
        raw = kelly_fraction(p, b=odds)
        size = raw * kelly_fraction_scale

    elif method == "sigmoid":
        # smoother than linear, still bounded
        # centred at 0.5 so prob=0.5 → size=0
        size = (2.0 / (1.0 + math.exp(-10.0 * (p - 0.5))) - 1.0) * kelly_fraction_scale

    elif method == "linear":
        # simplest: size = 2p - 1, scaled
        size = (2.0 * p - 1.0) * kelly_fraction_scale

    else:
        raise ValueError(f"Unknown method: {method}. Use fractional_kelly | sigmoid | linear")

    # apply direction
    if direction == "SELL":
        size = -abs(size)
    else:  # BUY
        size = abs(size)

    # hard cap
    size = float(np.clip(size, -max_position, max_position))
    return round(size, 4)


# ── BetSizer class (production use) ──────────────────────────────────────────

@dataclass
class BetSizer:
    """
    Stateful bet sizer — wraps compute_bet_size with config and adds
    meta-label confidence filtering.

    Usage:
        sizer = BetSizer(kelly_fraction=0.5, min_confidence=0.55)
        output = sizer.size_signal(signal_dict)
    """
    kelly_fraction:  float = DEFAULT_KELLY_FRACTION
    min_confidence:  float = DEFAULT_MIN_CONFIDENCE
    max_position:    float = DEFAULT_MAX_POSITION
    method:          str   = "fractional_kelly"
    odds:            float = 1.0

    def size_signal(self, signal: Dict) -> Dict:
        """
        Add position_size and kelly_raw to an existing signal dict.

        Expected input keys:
            direction   — "BUY" | "SELL" | "HOLD"
            probability — float 0-1 (model confidence for direction)

        Optional keys (used if present):
            meta_probability — secondary model confidence (filters weak signals)
        """
        signal = dict(signal)  # don't mutate original

        direction   = signal.get("direction", "HOLD")
        probability = float(signal.get("probability") or 0.5)

        # meta-label gate: if secondary model says low confidence, skip trade
        meta_prob = signal.get("meta_probability")
        if meta_prob is not None and float(meta_prob) < self.min_confidence:
            signal["position_size"] = 0.0
            signal["kelly_raw"]     = 0.0
            signal["sizing_note"]   = "filtered_by_meta_label"
            return signal

        # compute raw Kelly (full, before fraction scaling)
        raw_kelly = kelly_fraction(probability, b=self.odds)
        if direction == "SELL":
            raw_kelly = -abs(raw_kelly)

        size = compute_bet_size(
            probability=probability,
            direction=direction,
            method=self.method,
            kelly_fraction_scale=self.kelly_fraction,
            min_confidence=self.min_confidence,
            max_position=self.max_position,
            odds=self.odds,
        )

        signal["position_size"] = size
        signal["kelly_raw"]     = round(raw_kelly, 4)
        signal["sizing_note"]   = "ok" if size != 0.0 else "below_min_confidence"
        return signal

    def size_batch(self, signals: list) -> list:
        """Apply size_signal to a list of signal dicts."""
        return [self.size_signal(s) for s in signals]


# ── standalone helper (backward compat) ──────────────────────────────────────

_default_sizer = BetSizer()

def size_signal(signal: Dict) -> Dict:
    """Module-level convenience wrapper using default BetSizer config."""
    return _default_sizer.size_signal(signal)
