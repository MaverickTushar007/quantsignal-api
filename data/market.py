import yfinance as yf
import pandas as pd
import time

def fetch_ohlcv(ticker: str, period: str = "2y") -> pd.DataFrame | None:
    for attempt in range(3):
        try:
            t = yf.Ticker(ticker)
            df = t.history(period=period, auto_adjust=True)
            if df is not None and len(df) > 50:
                df.index = df.index.tz_localize(None) if df.index.tzinfo else df.index
                return df
        except Exception as e:
            print(f"Attempt {attempt+1} failed for {ticker}: {e}")
            time.sleep(2)
    return None
