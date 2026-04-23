"""
app/domain/ml/features.py

Feature engineering for QuantSignal — Phase 2.
Implements two key improvements from AFML (Advances in Financial ML):

1. Fractional Differentiation (Chapter 5)
   - Standard log returns destroy memory (d=1 differencing = fully stationary)
   - Raw prices are non-stationary (random walk, unit root)
   - Fractional diff finds d in (0,1) that is JUST stationary while keeping memory
   - Result: features that pass ADF test but retain long-range correlation

2. Dollar Bars (Chapter 2)
   - Time bars (OHLCV per day) have poor statistical properties
   - Dollar bars sample when $X of volume traded — activity-based sampling
   - More uniform volatility, better for ML (IID assumption holds better)
   - Fallback to time bars if tick data unavailable (which it usually is for free data)

Usage:
    from app.domain.ml.features import add_frac_diff_features, enrich_features

    # Add frac-diff price features to existing feature df
    df = add_frac_diff_features(df, cols=["Close", "Volume"], d=0.4)

    # Full enrichment pipeline (drop-in replacement for manual feature engineering)
    feat_df = enrich_features(df)
"""

from __future__ import annotations

import logging
from typing import List, Optional

import numpy as np
import pandas as pd
from statsmodels.tsa.stattools import adfuller

logger = logging.getLogger(__name__)


# ── Fractional Differentiation ─────────────────────────────────────────────────

def _get_weights(d: float, size: int) -> np.ndarray:
    """
    Compute binomial series weights for fractional differencing.
    w_k = prod_{i=0}^{k-1} (d - i) / (i + 1)
    """
    w = [1.0]
    for k in range(1, size):
        w.append(-w[-1] * (d - k + 1) / k)
    return np.array(w[::-1])  # oldest weight first


def _get_weights_ffd(d: float, thres: float = 1e-5) -> np.ndarray:
    """
    Fixed-width window weights (FFD method) — stops when weight < threshold.
    More practical than full-history weights for long series.
    """
    w = [1.0]
    k = 1
    while abs(w[-1]) > thres:
        w.append(-w[-1] * (d - k + 1) / k)
        k += 1
    return np.array(w[::-1])


def frac_diff_ffd(series: pd.Series, d: float, thres: float = 1e-5) -> pd.Series:
    """
    Apply fixed-width window fractional differencing to a price series.

    Args:
        series: raw price series (e.g. Close prices)
        d:      differencing order, 0 < d < 1
        thres:  weight cutoff threshold

    Returns:
        Fractionally differenced series (same index, NaNs at start)
    """
    w = _get_weights_ffd(d, thres)
    width = len(w)
    output = {}
    series_vals = series.values.astype(float)

    for i in range(width - 1, len(series_vals)):
        window = series_vals[i - width + 1: i + 1]
        output[series.index[i]] = float(np.dot(w, window))

    return pd.Series(output, name=series.name)


def find_min_d(series: pd.Series, max_d: float = 1.0, step: float = 0.1,
               pvalue_threshold: float = 0.05) -> float:
    """
    Find the minimum d such that frac_diff series passes ADF stationarity test.
    Scans d from 0 to max_d in steps.

    Returns: minimum d that achieves stationarity (default 0.4 if scan fails)
    """
    for d in np.arange(0, max_d + step, step):
        fd = frac_diff_ffd(series.dropna(), round(float(d), 2))
        if len(fd.dropna()) < 20:
            continue
        try:
            pval = adfuller(fd.dropna(), maxlag=1, regression="c", autolag=None)[1]
            if pval < pvalue_threshold:
                logger.debug(f"[frac_diff] min d={d:.2f} (ADF p={pval:.4f}) for {series.name}")
                return round(float(d), 2)
        except Exception:
            continue
    logger.warning(f"[frac_diff] Could not find stationary d for {series.name}, using 0.4")
    return 0.4


def add_frac_diff_features(
    df: pd.DataFrame,
    cols: Optional[List[str]] = None,
    d: Optional[float] = None,
    auto_d: bool = False,
) -> pd.DataFrame:
    """
    Add fractionally differenced versions of price columns to df.

    Args:
        df:      OHLCV dataframe
        cols:    columns to frac-diff (default: ["Close", "Volume"])
        d:       differencing order (if None and auto_d=False, uses 0.4)
        auto_d:  if True, find_min_d per column (slower but optimal)

    Returns:
        df with new columns: {col}_fd (fractionally differenced)
    """
    if cols is None:
        cols = [c for c in ["Close", "Volume"] if c in df.columns]

    result = df.copy()

    for col in cols:
        if col not in df.columns:
            logger.warning(f"[frac_diff] Column {col} not found, skipping")
            continue

        series = df[col].dropna()
        if len(series) < 30:
            logger.warning(f"[frac_diff] Not enough data for {col}, skipping")
            continue

        # log-transform prices before differencing (standard practice)
        if col in ["Close", "Open", "High", "Low"]:
            series = np.log(series.clip(lower=1e-8))

        use_d = d
        if use_d is None:
            use_d = find_min_d(series) if auto_d else 0.4

        try:
            fd_series = frac_diff_ffd(series, d=use_d)
            result[f"{col}_fd"] = fd_series
            logger.debug(f"[frac_diff] Added {col}_fd (d={use_d})")
        except Exception as e:
            logger.warning(f"[frac_diff] Failed for {col}: {e}")

    return result


# ── Dollar Bars (simplified — requires tick data, fallback to time bars) ────────

def make_dollar_bars(
    df: pd.DataFrame,
    dollar_threshold: Optional[float] = None,
) -> pd.DataFrame:
    """
    Construct dollar bars from OHLCV data.
    
    Since we use daily OHLCV (not tick data), this approximates dollar bars by
    grouping days where cumulative dollar volume crosses the threshold.
    True dollar bars need tick-level data.

    Args:
        df:                OHLCV dataframe with Close and Volume columns
        dollar_threshold:  $ volume per bar (auto-computed if None)

    Returns:
        Resampled dataframe with dollar-bar OHLCV
    """
    if "Close" not in df.columns or "Volume" not in df.columns:
        logger.warning("[dollar_bars] Need Close and Volume columns")
        return df

    df = df.copy()
    df["dollar_volume"] = df["Close"] * df["Volume"]

    if dollar_threshold is None:
        # use median daily dollar volume as threshold
        dollar_threshold = float(df["dollar_volume"].median())

    bars = []
    cum_dv = 0.0
    bar_open = None
    bar_high = -np.inf
    bar_low  =  np.inf
    bar_vol  = 0.0
    bar_start_idx = df.index[0]

    for idx, row in df.iterrows():
        if bar_open is None:
            bar_open = row["Open"] if "Open" in row else row["Close"]
            bar_start_idx = idx

        bar_high = max(bar_high, row["High"] if "High" in row else row["Close"])
        bar_low  = min(bar_low,  row["Low"]  if "Low"  in row else row["Close"])
        bar_vol  += row["Volume"]
        cum_dv   += row["dollar_volume"]

        if cum_dv >= dollar_threshold:
            bars.append({
                "Date":   idx,
                "Open":   bar_open,
                "High":   bar_high,
                "Low":    bar_low,
                "Close":  row["Close"],
                "Volume": bar_vol,
                "dollar_volume": cum_dv,
            })
            # reset
            bar_open = None
            bar_high = -np.inf
            bar_low  =  np.inf
            bar_vol  = 0.0
            cum_dv   = 0.0

    if not bars:
        logger.warning("[dollar_bars] No bars generated, returning original df")
        return df

    result = pd.DataFrame(bars).set_index("Date")
    logger.info(f"[dollar_bars] {len(df)} time bars → {len(result)} dollar bars")
    return result


# ── Full enrichment pipeline ───────────────────────────────────────────────────

def enrich_features(
    df: pd.DataFrame,
    add_frac_diff: bool = True,
    add_dollar_bars: bool = False,  # off by default — needs sufficient volume data
    frac_diff_d: float = 0.4,
) -> pd.DataFrame:
    """
    Full feature enrichment pipeline. Drop-in for manual feature engineering.

    Adds:
      - Close_fd, Volume_fd  (fractionally differenced — stationary with memory)
      - rolling stats on fd series
      - dollar bar flag (if enabled)

    Args:
        df:             OHLCV dataframe (from fetch_ohlcv)
        add_frac_diff:  add fractionally differenced features
        add_dollar_bars: resample to dollar bars first
        frac_diff_d:    d parameter (0.4 is good default for daily prices)

    Returns:
        Enriched dataframe ready for model training
    """
    result = df.copy()

    # optionally resample to dollar bars
    if add_dollar_bars and "Volume" in df.columns:
        try:
            result = make_dollar_bars(result)
        except Exception as e:
            logger.warning(f"[enrich] dollar bars failed: {e}, using time bars")

    # fractional differentiation
    if add_frac_diff:
        try:
            result = add_frac_diff_features(result, d=frac_diff_d)

            # rolling stats on fd series (extra signal)
            for col in ["Close_fd", "Volume_fd"]:
                if col in result.columns:
                    result[f"{col}_zscore"] = (
                        (result[col] - result[col].rolling(20).mean())
                        / result[col].rolling(20).std().clip(lower=1e-8)
                    )
                    result[f"{col}_momentum"] = result[col].diff(5)

        except Exception as e:
            logger.warning(f"[enrich] frac diff failed: {e}")

    return result


# ── Integration helper: patch into existing feature pipeline ──────────────────

def patch_feature_df(feat_df: pd.DataFrame, raw_df: pd.DataFrame) -> pd.DataFrame:
    """
    Add frac-diff features to an already-computed feature dataframe.
    Call this after your existing feature engineering, before model.fit().

    Args:
        feat_df: existing features dataframe
        raw_df:  raw OHLCV dataframe (same index)

    Returns:
        feat_df with frac-diff columns added
    """
    enriched = add_frac_diff_features(raw_df, d=0.4)
    fd_cols = [c for c in enriched.columns if c.endswith("_fd")]

    for col in fd_cols:
        feat_df[col] = enriched[col].reindex(feat_df.index)

    logger.info(f"[patch_feature_df] Added {len(fd_cols)} frac-diff features: {fd_cols}")
    return feat_df


# ── build_features + FEATURE_COLUMNS (required by ensemble.py) ────────────────

FEATURE_COLUMNS = [
    "return_1d", "return_5d", "return_10d", "return_20d",
    "volatility_10d", "volatility_20d",
    "rsi_14", "rsi_28",
    "macd", "macd_signal", "macd_hist",
    "bb_upper", "bb_lower", "bb_width", "bb_pct",
    "sma_10", "sma_20", "sma_50", "sma_200",
    "ema_12", "ema_26",
    "price_to_sma20", "price_to_sma50",
    "volume_ratio_10d", "volume_ratio_20d",
    "atr_14", "atr_pct",
    "obv_change",
    "high_low_range", "close_position",
    "momentum_10d", "momentum_20d",
]


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """Build ML feature matrix matching saved model feature names."""
    df = df.copy()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    close  = df["Close"]
    high   = df["High"]   if "High"   in df.columns else close
    low    = df["Low"]    if "Low"    in df.columns else close
    volume = df["Volume"] if "Volume" in df.columns else pd.Series(1, index=df.index)

    feat = pd.DataFrame(index=df.index)

    feat["return_1d"]  = close.pct_change(1)
    feat["return_5d"]  = close.pct_change(5)
    feat["return_10d"] = close.pct_change(10)
    feat["return_20d"] = close.pct_change(20)

    feat["volatility_10d"] = feat["return_1d"].rolling(10).std()
    feat["volatility_20d"] = feat["return_1d"].rolling(20).std()

    delta = close.diff()
    gain  = delta.clip(lower=0).rolling(14).mean()
    loss  = (-delta.clip(upper=0)).rolling(14).mean()
    feat["rsi_14"] = 100 - 100 / (1 + gain / loss.clip(lower=1e-8))
    gain2 = delta.clip(lower=0).rolling(28).mean()
    loss2 = (-delta.clip(upper=0)).rolling(28).mean()
    feat["rsi_28"] = 100 - 100 / (1 + gain2 / loss2.clip(lower=1e-8))

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd  = ema12 - ema26
    feat["macd"]        = macd
    feat["macd_signal"] = macd.ewm(span=9, adjust=False).mean()
    feat["macd_hist"]   = macd - feat["macd_signal"]
    feat["ema_12"]      = ema12
    feat["ema_26"]      = ema26

    sma20 = close.rolling(20).mean()
    std20 = close.rolling(20).std()
    feat["bb_upper"] = sma20 + 2 * std20
    feat["bb_lower"] = sma20 - 2 * std20
    feat["bb_width"] = (feat["bb_upper"] - feat["bb_lower"]) / sma20.clip(lower=1e-8)
    feat["bb_pct"]   = (close - feat["bb_lower"]) / (feat["bb_upper"] - feat["bb_lower"]).clip(lower=1e-8)

    feat["sma_10"]  = close.rolling(10).mean()
    feat["sma_20"]  = sma20
    feat["sma_50"]  = close.rolling(50).mean()
    feat["sma_200"] = close.rolling(200).mean()
    feat["price_to_sma20"] = close / sma20.clip(lower=1e-8) - 1
    feat["price_to_sma50"] = close / feat["sma_50"].clip(lower=1e-8) - 1

    feat["volume_ratio_10d"] = volume / volume.rolling(10).mean().clip(lower=1e-8)
    feat["volume_ratio_20d"] = volume / volume.rolling(20).mean().clip(lower=1e-8)
    obv = (np.sign(close.diff()) * volume).fillna(0).cumsum()
    feat["obv_change"] = obv.pct_change(5)

    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low  - close.shift()).abs(),
    ], axis=1).max(axis=1)
    feat["atr_14"]  = tr.rolling(14).mean()
    feat["atr_pct"] = feat["atr_14"] / close.clip(lower=1e-8)

    feat["high_low_range"] = (high - low) / close.clip(lower=1e-8)
    feat["close_position"] = (close - low) / (high - low).clip(lower=1e-8)
    feat["momentum_10d"]   = close / close.shift(10).clip(lower=1e-8) - 1
    feat["momentum_20d"]   = close / close.shift(20).clip(lower=1e-8) - 1

    feat = feat.replace([np.inf, -np.inf], np.nan)
    return feat

