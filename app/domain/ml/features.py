"""
ml/features.py
Feature engineering pipeline — 12 higher-signal features.

Dropped: RSI_7, MACD_signal, MACD_hist, stoch_K, stoch_D, vol_trend,
         SMA_cross, dist_SMA20, pos_52w, ATR_pct, range_pct, ret_vol (redundant lagging)

Added:
  - overnight_gap    : open vs prev close (informed order flow)
  - vol_price_div    : volume direction vs price direction divergence
  - mean_rev_z       : z-score distance from 20d mean in ATR units (mean reversion)
  - vol_regime       : ATR expanding vs contracting (volatility regime)
  - rsi_divergence   : price making new high but RSI not (hidden weakness)
  - body_ratio       : candle body / range (conviction of move)
  - ret_skew         : 20d return skewness (tail risk direction)
"""
import numpy as np
import pandas as pd


def _rsi(series: pd.Series, period: int) -> pd.Series:
    delta = series.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _atr(high, low, close, period=14):
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low  - close.shift()).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    close = df["Close"].astype(float)
    high  = df["High"].astype(float)
    low   = df["Low"].astype(float)
    open_ = df["Open"].astype(float)
    vol   = df["Volume"].astype(float).replace(0, np.nan).fillna(1.0)

    feat  = pd.DataFrame(index=df.index)
    atr14 = _atr(high, low, close, 14)

    # --- Kept from original (proven signal) ---
    feat["RSI_14"]      = _rsi(close, 14)
    ema12               = close.ewm(span=12, adjust=False).mean()
    ema26               = close.ewm(span=26, adjust=False).mean()
    feat["MACD"]        = (ema12 - ema26) / close.replace(0, np.nan)  # normalised
    sma20               = close.rolling(20).mean()
    std20               = close.rolling(20).std()
    bb_upper            = sma20 + 2 * std20
    bb_lower            = sma20 - 2 * std20
    bb_range            = (bb_upper - bb_lower).replace(0, np.nan)
    feat["BB_pct"]      = (close - bb_lower) / bb_range
    feat["BB_width"]    = bb_range / sma20.replace(0, np.nan)
    vol_avg20           = vol.rolling(20).mean().replace(0, np.nan)
    feat["vol_ratio"]   = vol / vol_avg20
    sma50               = close.rolling(50).mean().replace(0, np.nan)
    feat["dist_SMA50"]  = (close - sma50) / sma50
    feat["mom_5d"]      = close.pct_change(5)
    feat["mom_20d"]     = close.pct_change(20)

    # --- New higher-signal features ---

    # 1. Overnight gap: open vs previous close (informed order flow signal)
    feat["overnight_gap"] = (open_ - close.shift(1)) / close.shift(1).replace(0, np.nan)

    # 2. Volume-price divergence: vol direction vs price direction
    price_dir = np.sign(close.diff())
    vol_dir   = np.sign(vol.diff())
    feat["vol_price_div"] = (price_dir * vol_dir).rolling(5).mean()

    # 3. Mean reversion z-score in ATR units
    feat["mean_rev_z"] = (close - sma20) / atr14.replace(0, np.nan)

    # 4. Volatility regime: is ATR expanding or contracting?
    atr5 = _atr(high, low, close, 5)
    feat["vol_regime"] = atr5 / atr14.replace(0, np.nan)

    # 5. RSI divergence: price 5d change vs RSI 5d change (hidden strength/weakness)
    rsi14 = feat["RSI_14"]
    feat["rsi_divergence"] = close.pct_change(5) - rsi14.diff(5) / 100

    # 6. Candle body ratio: body / range = conviction (1 = full conviction, 0 = doji)
    body  = (close - open_).abs()
    range_ = (high - low).replace(0, np.nan)
    feat["body_ratio"] = body / range_

    # 7. Return skewness over 20 days (tail risk direction)
    feat["ret_skew"] = close.pct_change().rolling(20).skew()

    feat = feat.replace([np.inf, -np.inf], np.nan).dropna()
    return feat


FEATURE_COLUMNS = [
    "RSI_14", "MACD", "BB_pct", "BB_width",
    "vol_ratio", "dist_SMA50", "mom_5d", "mom_20d",
    "overnight_gap", "vol_price_div", "mean_rev_z",
    "vol_regime", "rsi_divergence", "body_ratio", "ret_skew",
]
assert len(FEATURE_COLUMNS) == 15
