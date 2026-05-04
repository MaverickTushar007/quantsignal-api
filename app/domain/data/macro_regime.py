"""
macro_regime.py — Discrete Macro Regime Labeler for QuantSignal

Academic basis:
  - "A Regime-Switching Heston Model for VIX and S&P 500 Implied Volatilities" — Princeton:
    https://economics.princeton.edu/published-papers/a-regime-switching-heston-model-for-vix-and-sp-500-implied-volatilities/
  - "A Regime-Switching Model of the Yield Curve at the Zero Bound" — FRBSF:
    https://www.frbsf.org/wp-content/uploads/wp2013-34.pdf
  - "Statistical Jump Models for Regime Switching":
    https://www.bscapitalmarkets.com/statistical-jump-models-for-regime-switching.html
  - "Volatility Regime Shifting — Detecting Market Shifts Early":
    https://www.dozendiamonds.com/volatility-regime-shifting/
  - "Market Regimes Explained" — LuxAlgo:
    https://www.luxalgo.com/blog/market-regimes-explained-build-winning-trading-strategies/
  - Exchange Rate Forecasting, Order Flow and Macro Information — Bank of Canada:
    https://www.bankofcanada.ca/wp-content/uploads/2010/09/rime2.pdf

What it does:
  Combines VIX level, yield curve shape, CPI surprise, and Fed regime
  into 4 discrete macro regime labels:

  RISK_ON      : VIX low, yield curve normal, low inflation → full signal weight
  RISK_OFF     : VIX high, yield curve inverted → reduce position sizes
  INFLATION    : CPI surprise high, rate hike regime → bearish equities/crypto
  STAGFLATION  : recession signal + inflation → maximum caution

  Regime label fed into ensemble.py to scale final probability adjustments.

Data sources:
  - FRED API (already in your stack via fredapi):
    VIX: VIXCLS, Yield spread: T10Y2Y, CPI: CPIAUCSL, Fed Funds: FEDFUNDS
  - Ref: https://fred.stlouisfed.org/docs/api/fred/
"""

from __future__ import annotations
import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

BASE_DIR      = Path(__file__).resolve().parents[3]
REGIME_CACHE  = BASE_DIR / "data" / "macro_regime_cache.json"
REGIME_CACHE.parent.mkdir(parents=True, exist_ok=True)

REGIME_CACHE_HOURS = 6   # refresh every 6 hours

# ── Regime definitions ────────────────────────────────────────────────────────
REGIME_RISK_ON     = "RISK_ON"
REGIME_RISK_OFF    = "RISK_OFF"
REGIME_INFLATION   = "INFLATION"
REGIME_STAGFLATION = "STAGFLATION"

# Thresholds (calibrated for US macro, adjust for Indian market regime if needed)
VIX_HIGH_THRESH     = 25.0   # VIX > 25 = risk-off
VIX_EXTREME_THRESH  = 35.0   # VIX > 35 = crisis
YIELD_INVERT_THRESH = -0.10  # 10y-2y < -0.10% = inverted
CPI_SURPRISE_THRESH = 0.30   # CPI YoY > 4% AND accelerating


def _load_macro_from_existing() -> dict:
    """
    Pull macro data from your existing macro.py module.
    Avoids duplicating FRED calls — reuses what's already cached.
    """
    try:
        from app.domain.data.macro import get_macro_features
        return get_macro_features()
    except Exception as e:
        logger.warning(f"[macro_regime] Could not load from macro.py: {e}")
        return {}


def get_macro_regime() -> dict:
    """
    Returns current macro regime classification.

    Returns:
      regime        : str  — RISK_ON | RISK_OFF | INFLATION | STAGFLATION
      regime_score  : float — 0 (most bullish) to 1 (most bearish)
      prob_multiplier: float — multiply final prob adjustment by this
                              RISK_ON=1.2, RISK_OFF=0.6, INFLATION=0.7, STAGFLATION=0.4
      details       : dict  — individual factor states
      cached_at     : str   — ISO timestamp
    """
    # Check cache
    if REGIME_CACHE.exists():
        try:
            cached = json.loads(REGIME_CACHE.read_text())
            age_hours = (datetime.utcnow() - datetime.fromisoformat(
                cached.get("cached_at", "2000-01-01"))).total_seconds() / 3600
            if age_hours < REGIME_CACHE_HOURS:
                return cached
        except Exception:
            pass

    macro = _load_macro_from_existing()

    vix          = macro.get("vix", 20.0)
    yield_spread = macro.get("yield_spread_10y2y", 0.5)
    cpi_yoy      = macro.get("cpi_yoy", 2.5)
    rate_hike    = macro.get("rate_hike_regime", 0)
    recession    = macro.get("recession_signal", 0)
    high_fear    = macro.get("high_fear", 0)
    inflation_hi = macro.get("inflation_high", 0)

    # ── Classify regime ───────────────────────────────────────────────────────
    # Priority order: STAGFLATION > INFLATION > RISK_OFF > RISK_ON

    details = {
        "vix":          vix,
        "yield_spread": yield_spread,
        "cpi_yoy":      cpi_yoy,
        "rate_hike":    rate_hike,
        "recession":    recession,
    }

    if recession and inflation_hi:
        regime = REGIME_STAGFLATION
        regime_score    = 0.90
        prob_multiplier = 0.40   # scale all signal adjustments to 40%

    elif inflation_hi and rate_hike:
        regime = REGIME_INFLATION
        regime_score    = 0.70
        prob_multiplier = 0.65

    elif vix > VIX_HIGH_THRESH or yield_spread < YIELD_INVERT_THRESH or high_fear:
        regime = REGIME_RISK_OFF
        regime_score    = 0.60
        prob_multiplier = 0.60

    else:
        regime = REGIME_RISK_ON
        regime_score    = 0.20
        prob_multiplier = 1.20   # amplify signals in risk-on

    result = {
        "regime":          regime,
        "regime_score":    round(regime_score, 4),
        "prob_multiplier": round(prob_multiplier, 4),
        "details":         details,
        "cached_at":       datetime.utcnow().isoformat(),
    }

    try:
        REGIME_CACHE.write_text(json.dumps(result, indent=2))
    except Exception:
        pass

    logger.info(f"[macro_regime] {regime} (score={regime_score}, mult={prob_multiplier})")
    return result


def apply_regime_to_prob(prob: float, direction: str) -> float:
    """
    Scale a signal probability by the current macro regime.

    In RISK_ON regime: amplify conviction (prob moves further from 0.5)
    In RISK_OFF/STAGFLATION: compress toward 0.5 (reduce all positions)

    Usage in ensemble.py (Phase 3):
        from app.domain.data.macro_regime import apply_regime_to_prob
        prob = apply_regime_to_prob(prob, direction)
    """
    try:
        regime_data = get_macro_regime()
        mult = regime_data.get("prob_multiplier", 1.0)
        # Compress/amplify distance from 0.5
        deviation = prob - 0.5
        new_prob  = 0.5 + deviation * mult
        return round(max(0.01, min(0.99, new_prob)), 4)
    except Exception as e:
        logger.warning(f"[macro_regime] apply failed: {e}")
        return prob
