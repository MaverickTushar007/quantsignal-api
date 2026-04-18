"""
data/mtf.py
Multi-timeframe feature extraction.
Pulls 1H, 4H, 15min data and computes trend alignment features.
"""
import pandas as pd
import numpy as np
from typing import Optional
import logging
log = logging.getLogger(__name__)

def fetch_mtf_features(symbol: str) -> dict:
    """
    Returns MTF alignment dict for a symbol:
    {
        tf_1h_bull: bool,
        tf_4h_bull: bool,
        tf_15m_bull: bool,
        tf_1h_rsi: float,
        tf_4h_rsi: float,
        mtf_score: int (0-4, how many TFs are bullish),
        mtf_details: {"15m": "BULL"/"BEAR", "1h": ..., "4h": ..., "1d": ...}
    }
    """
    try:
        # Fetch 1H — try Yahoo direct first, fall back to yfinance
        df_1h = None
        try:
            import requests
            headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1h&range=60d"
            r = requests.get(url, headers=headers, timeout=12)
            result = r.json().get("chart", {}).get("result", [])
            if result:
                ts = result[0].get("timestamp", [])
                q = result[0]["indicators"]["quote"][0]
                df_1h = pd.DataFrame({
                    "Open": q.get("open"), "High": q.get("high"),
                    "Low": q.get("low"), "Close": q.get("close"), "Volume": q.get("volume"),
                }, index=pd.to_datetime(ts, unit="s"))
                df_1h = df_1h.dropna(subset=["Close"])
                df_1h.index = df_1h.index.tz_localize(None) if df_1h.index.tzinfo else df_1h.index
                log.info(f"[mtf] yahoo_direct 1h OK for {symbol}: {len(df_1h)} bars")
        except Exception as e:
            log.debug(f"[mtf] yahoo_direct 1h failed for {symbol}: {e}")

        if df_1h is None or len(df_1h) < 20:
            try:
                import yfinance as yf
                df_1h = yf.Ticker(symbol).history(period="60d", interval="1h")
                if df_1h is not None:
                    df_1h.index = df_1h.index.tz_localize(None) if df_1h.index.tzinfo else df_1h.index
                log.info(f"[mtf] yfinance 1h fallback OK for {symbol}")
            except Exception as e:
                log.debug(f"[mtf] yfinance 1h fallback failed for {symbol}: {e}")

        if df_1h is None or len(df_1h) < 20:
            return _neutral_mtf()

        # Build 4H by resampling
        df_4h = df_1h.resample("4h").agg({
            "Open": "first", "High": "max",
            "Low": "min", "Close": "last", "Volume": "sum"
        }).dropna()

        # Fetch 15min — Yahoo direct first, yfinance fallback
        df_15m = None
        try:
            import requests
            headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=15m&range=5d"
            r = requests.get(url, headers=headers, timeout=12)
            result = r.json().get("chart", {}).get("result", [])
            if result:
                ts = result[0].get("timestamp", [])
                q = result[0]["indicators"]["quote"][0]
                df_15m = pd.DataFrame({
                    "Open": q.get("open"), "High": q.get("high"),
                    "Low": q.get("low"), "Close": q.get("close"), "Volume": q.get("volume"),
                }, index=pd.to_datetime(ts, unit="s"))
                df_15m = df_15m.dropna(subset=["Close"])
                df_15m.index = df_15m.index.tz_localize(None) if df_15m.index.tzinfo else df_15m.index
        except Exception as e:
            log.debug(f"[mtf] yahoo_direct 15m failed for {symbol}: {e}")

        if df_15m is None or len(df_15m) < 5:
            try:
                import yfinance as yf
                df_15m = yf.Ticker(symbol).history(period="5d", interval="15m")
                if df_15m is not None:
                    df_15m.index = df_15m.index.tz_localize(None) if df_15m.index.tzinfo else df_15m.index
            except Exception as e:
                log.debug(f"[mtf] yfinance 15m fallback failed for {symbol}: {e}")

        if df_15m is None:
            df_15m = pd.DataFrame()

        def ema(series, n):
            return series.ewm(span=n, adjust=False).mean()

        def rsi(series, n=14):
            delta = series.diff()
            gain = delta.clip(lower=0).rolling(n).mean()
            loss = (-delta.clip(upper=0)).rolling(n).mean()
            rs = gain / (loss + 1e-10)
            return 100 - (100 / (1 + rs))

        # 1H signals
        c1h = df_1h['Close']
        ema20_1h = ema(c1h, 20).iloc[-1]
        ema50_1h = ema(c1h, 50).iloc[-1]
        rsi_1h = rsi(c1h).iloc[-1]
        price_1h = c1h.iloc[-1]
        bull_1h = bool(price_1h > ema20_1h and rsi_1h > 45)

        # 4H signals
        c4h = df_4h['Close']
        ema20_4h = ema(c4h, 20).iloc[-1]
        rsi_4h = rsi(c4h).iloc[-1]
        price_4h = c4h.iloc[-1]
        bull_4h = bool(price_4h > ema20_4h and rsi_4h > 45)

        # 15min momentum
        bull_15m = False
        if len(df_15m) >= 10:
            c15m = df_15m['Close']
            ema9_15m = ema(c15m, 9).iloc[-1]
            price_15m = c15m.iloc[-1]
            recent_change = (c15m.iloc[-1] - c15m.iloc[-5]) / c15m.iloc[-5] * 100
            bull_15m = bool(price_15m > ema9_15m and recent_change > 0)

        # MTF score (0-4): 15m + 1h + 4h + placeholder for daily
        # Daily direction comes from the main signal, pass it separately
        mtf_score = sum([bull_15m, bull_1h, bull_4h])  # 0-3 from intraday TFs

        return {
            'tf_15m_bull': bull_15m,
            'tf_1h_bull': bull_1h,
            'tf_4h_bull': bull_4h,
            'tf_1h_rsi': round(float(rsi_1h), 1),
            'tf_4h_rsi': round(float(rsi_4h), 1),
            'mtf_score': mtf_score,
            'mtf_details': {
                '15m': 'BULL' if bull_15m else 'BEAR',
                '1h':  'BULL' if bull_1h  else 'BEAR',
                '4h':  'BULL' if bull_4h  else 'BEAR',
            }
        }

    except Exception as e:
        print(f"MTF fetch failed for {symbol}: {e}")
        return _neutral_mtf()

def _neutral_mtf() -> dict:
    return {
        'tf_15m_bull': False, 'tf_1h_bull': False, 'tf_4h_bull': False,
        'tf_1h_rsi': 50.0, 'tf_4h_rsi': 50.0, 'mtf_score': 0,
        'mtf_details': {'15m': 'NEUTRAL', '1h': 'NEUTRAL', '4h': 'NEUTRAL'}
    }
