"""
ml/features.py
Feature engineering pipeline — 21 technical indicators from raw OHLCV data.
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
    vol   = df["Volume"].astype(float).replace(0, np.nan).fillna(1.0)  # forex/VIX have no volume
    feat  = pd.DataFrame(index=df.index)

    feat["RSI_14"]      = _rsi(close, 14)
    feat["RSI_7"]       = _rsi(close, 7)

    ema12               = close.ewm(span=12, adjust=False).mean()
    ema26               = close.ewm(span=26, adjust=False).mean()
    feat["MACD"]        = ema12 - ema26
    feat["MACD_signal"] = feat["MACD"].ewm(span=9, adjust=False).mean()
    feat["MACD_hist"]   = feat["MACD"] - feat["MACD_signal"]

    sma20               = close.rolling(20).mean()
    std20               = close.rolling(20).std()
    bb_upper            = sma20 + 2 * std20
    bb_lower            = sma20 - 2 * std20
    bb_range            = (bb_upper - bb_lower).replace(0, np.nan)
    feat["BB_pct"]      = (close - bb_lower) / bb_range
    feat["BB_width"]    = bb_range / sma20.replace(0, np.nan)

    low14               = low.rolling(14).min()
    high14              = high.rolling(14).max()
    stoch_range         = (high14 - low14).replace(0, np.nan)
    feat["stoch_K"]     = 100 * (close - low14) / stoch_range
    feat["stoch_D"]     = feat["stoch_K"].rolling(3).mean()

    vol_avg20           = vol.rolling(20).mean().replace(0, np.nan)
    vol_avg5            = vol.rolling(5).mean().replace(0, np.nan)
    feat["vol_ratio"]   = vol / vol_avg20
    feat["vol_trend"]   = vol_avg5 / vol_avg20

    sma50               = close.rolling(50).mean().replace(0, np.nan)
    feat["SMA_cross"]   = sma20 / sma50
    feat["dist_SMA20"]  = (close - sma20) / sma20.replace(0, np.nan)
    feat["dist_SMA50"]  = (close - sma50) / sma50

    _w52 = min(252, len(close))
    low52               = low.rolling(_w52, min_periods=1).min()
    high52              = high.rolling(_w52, min_periods=1).max()
    range52             = (high52 - low52).replace(0, np.nan)
    feat["pos_52w"]     = (close - low52) / range52

    feat["mom_5d"]      = close.pct_change(5)
    feat["mom_10d"]     = close.pct_change(10)
    feat["mom_20d"]     = close.pct_change(20)

    feat["ATR_pct"]     = _atr(high, low, close, 14) / close.replace(0, np.nan)
    feat["range_pct"]   = (high - low) / close.replace(0, np.nan)
    feat["ret_vol"]     = close.pct_change().rolling(20).std()

    feat = feat.replace([np.inf, -np.inf], np.nan).dropna()
    return feat


FEATURE_COLUMNS = [
    "RSI_14", "RSI_7",
    "MACD", "MACD_signal", "MACD_hist",
    "BB_pct", "BB_width",
    "stoch_K", "stoch_D",
    "vol_ratio", "vol_trend",
    "SMA_cross", "dist_SMA20", "dist_SMA50",
    "pos_52w",
    "mom_5d", "mom_10d", "mom_20d",
    "ATR_pct", "range_pct", "ret_vol",
]

assert len(FEATURE_COLUMNS) == 21
