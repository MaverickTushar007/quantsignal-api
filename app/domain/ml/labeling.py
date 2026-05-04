"""
labeling.py — Triple Barrier Labeling for QuantSignal

Replaces naive "price up 5 days later = BUY" labels with proper
triple barrier labels from López de Prado (AFML, Chapter 3).

Academic basis:
  - López de Prado (2018) "Advances in Financial Machine Learning" Ch.3
  - Triple Barrier Method — Interactive Brokers Campus:
    https://www.interactivebrokers.com/campus/ibkr-quant-news/the-triple-barrier-method-a-python-gpu-based-computation-part-i/
  - Quantreo triple barrier overview:
    https://www.newsletter.quantreo.com/p/the-triple-barrier-labeling-of-marco
  - arXiv 2504.02249 (triple barrier + raw time series for stock prediction):
    https://arxiv.org/abs/2504.02249
  - mlfinlab reference implementation (Hudson & Thames):
    https://github.com/hudson-and-thames/mlfinlab

Label logic:
  For each row t with entry price p0:
    Upper barrier: p0 * (1 + pt_mult * daily_vol)   → BUY  (1)
    Lower barrier: p0 * (1 - sl_mult * daily_vol)   → SELL (−1)
    Vertical barrier: t + num_days                  → HOLD  (0)

  First barrier hit determines label.
  bin=1: TP hit first (BUY)
  bin=0: SL hit first (SELL)
  bin=-1 (HOLD) mapped to 0 in binary classification

This replaces the naive 5-day return threshold used in ensemble.py fallback.
"""

from __future__ import annotations
import logging
import numpy as np
import pandas as pd
from typing import Optional

logger = logging.getLogger(__name__)


def _daily_vol(close: pd.Series, span: int = 20) -> pd.Series:
    """
    Compute exponentially weighted daily volatility (std of log returns).
    Used to scale barrier width — wider barriers in volatile regimes.
    Ref: AFML Ch.3, Eq. 3.1
    """
    log_ret = np.log(close / close.shift(1)).dropna()
    vol = log_ret.ewm(span=span, min_periods=span // 2).std()
    return vol.reindex(close.index).bfill().fillna(0.01)


def build_triple_barrier_labels(
    df: pd.DataFrame,
    pt_mult: float = 2.0,      # profit-take multiplier (ATR units)
    sl_mult: float = 1.0,      # stop-loss multiplier (ATR units)
    num_days: int = 5,         # vertical barrier width in trading days
    min_ret: float = 0.001,    # minimum return to trigger a label (noise filter)
    vol_span: int = 20,        # EWM span for volatility estimation
) -> pd.DataFrame:
    """
    Apply triple barrier labeling to an OHLCV dataframe.

    Args:
        df:       OHLCV dataframe (daily bars)
        pt_mult:  profit-take = pt_mult * daily_vol above entry
        sl_mult:  stop-loss   = sl_mult * daily_vol below entry
        num_days: max holding period in bars
        min_ret:  minimum |return| to label as 1 or -1 (else 0)
        vol_span: EWM span for volatility

    Returns:
        DataFrame with columns:
          t1    : timestamp of first barrier hit
          ret   : return from entry to first barrier
          bin   : 1 (TP hit), -1 (SL hit), 0 (time barrier / hold)
          bin_binary: 1 (TP hit = BUY), 0 (everything else = not-BUY)

    Usage in ensemble.py train():
        from app.domain.ml.labeling import build_triple_barrier_labels
        labeled = build_triple_barrier_labels(df, pt_mult=2.0, sl_mult=1.0, num_days=5)
        y = labeled["bin_binary"]
    """
    if isinstance(df.columns, pd.MultiIndex):
        df = df.copy()
        df.columns = df.columns.get_level_values(0)

    close = df["Close"].dropna()
    if len(close) < num_days + vol_span:
        logger.warning("[labeling] Not enough bars for triple barrier")
        return pd.DataFrame()

    vol = _daily_vol(close, span=vol_span)

    records = []
    close_vals = close.values
    close_idx  = close.index

    for i in range(len(close_vals) - num_days):
        t0   = close_idx[i]
        p0   = close_vals[i]
        v0   = float(vol.iloc[i])

        if v0 < min_ret:
            v0 = min_ret

        upper = p0 * (1 + pt_mult * v0)
        lower = p0 * (1 - sl_mult * v0)

        # Search forward up to num_days bars
        t1    = close_idx[min(i + num_days, len(close_vals) - 1)]
        ret   = 0.0
        label = 0   # default: time barrier (HOLD)

        for j in range(i + 1, min(i + num_days + 1, len(close_vals))):
            p_j = close_vals[j]
            if p_j >= upper:
                label = 1                          # TP hit → BUY
                t1    = close_idx[j]
                ret   = (p_j - p0) / p0
                break
            elif p_j <= lower:
                label = -1                         # SL hit → SELL
                t1    = close_idx[j]
                ret   = (p_j - p0) / p0
                break
        else:
            # Time barrier: use final price in window
            p_final = close_vals[min(i + num_days, len(close_vals) - 1)]
            ret     = (p_final - p0) / p0
            # Label by return magnitude at time barrier
            if ret > min_ret:
                label = 1
            elif ret < -min_ret:
                label = -1
            else:
                label = 0

        records.append({
            "t0":  t0,
            "t1":  t1,
            "ret": round(ret, 6),
            "bin": label,
            "bin_binary": 1 if label == 1 else 0,
        })

    result = pd.DataFrame(records).set_index("t0")
    result.index.name = None

    n_buy  = (result["bin"] ==  1).sum()
    n_sell = (result["bin"] == -1).sum()
    n_hold = (result["bin"] ==  0).sum()
    logger.info(
        f"[labeling] triple barrier: {len(result)} labels | "
        f"BUY={n_buy} ({n_buy/len(result)*100:.1f}%) "
        f"SELL={n_sell} ({n_sell/len(result)*100:.1f}%) "
        f"HOLD={n_hold} ({n_hold/len(result)*100:.1f}%)"
    )
    return result


def meta_label(
    primary_labels: pd.Series,
    primary_probs:  pd.Series,
    prob_threshold: float = 0.55,
) -> pd.DataFrame:
    """
    Meta-labeling (AFML Ch.3 extension).
    
    Given primary model predictions + probabilities, create a secondary
    binary label: "was the primary model correct AND confident enough?"
    
    This is Phase 3 — a second model learns WHEN to trust the primary model.
    Ref: López de Prado AFML Ch.3, mlfinlab meta-labeling docs.

    Args:
        primary_labels: pd.Series of predicted labels (BUY=1/SELL=0)
        primary_probs:  pd.Series of predicted probabilities
        prob_threshold: minimum prob for meta-label = 1

    Returns:
        DataFrame with meta_label column (1 = trust primary, 0 = skip)
    """
    df = pd.DataFrame({
        "primary_label": primary_labels,
        "primary_prob":  primary_probs,
    })
    df["meta_label"] = (
        (df["primary_prob"] >= prob_threshold).astype(int)
    )
    logger.info(
        f"[meta_label] {df['meta_label'].sum()} / {len(df)} signals pass meta threshold {prob_threshold}"
    )
    return df
