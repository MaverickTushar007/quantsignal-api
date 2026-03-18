"""
data/market.py
Fetches OHLCV price data from yFinance.
Single responsibility: symbol in, clean DataFrame out.
"""

import pandas as pd
import yfinance as yf
from typing import Optional


def fetch_ohlcv(symbol: str, period: str = "2y") -> Optional[pd.DataFrame]:
    """
    Fetch daily OHLCV data for a symbol.
    Returns clean DataFrame with columns: Open, High, Low, Close, Volume
    Returns None if fetch fails or insufficient data.
    """
    try:
        df = yf.download(symbol, period=period, progress=False, auto_adjust=True)
        if df is None or len(df) < 100:
            return None
        # Flatten multi-level columns if present
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
        # Keep only OHLCV
        df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
        df.dropna(inplace=True)
        return df
    except Exception as e:
        print(f"Failed to fetch {symbol}: {e}")
        return None


def fetch_current_price(symbol: str) -> Optional[float]:
    """Get just the latest closing price."""
    try:
        df = yf.download(symbol, period="5d", progress=False, auto_adjust=True)
        if df is None or df.empty:
            return None
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
        return float(df["Close"].iloc[-1])
    except Exception:
        return None
