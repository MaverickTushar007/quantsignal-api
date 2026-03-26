"""
data/nse.py
NSE India direct API for Indian stock OHLCV data.
Used instead of yfinance for .NS symbols — more reliable from Railway.
"""
import requests
import pandas as pd
from datetime import datetime, timedelta

NSE_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept': 'application/json',
    'Accept-Language': 'en-US,en;q=0.9',
    'Referer': 'https://www.nseindia.com',
}

def _get_session():
    s = requests.Session()
    try:
        s.get('https://www.nseindia.com', headers=NSE_HEADERS, timeout=10)
    except:
        pass
    return s

def fetch_nse_ohlcv(symbol: str, days: int = 365) -> pd.DataFrame | None:
    """
    Fetch OHLCV data for an NSE symbol (e.g. 'RELIANCE', 'TCS').
    Returns a DataFrame with columns: Open, High, Low, Close, Volume
    """
    try:
        session = _get_session()
        end = datetime.now()
        start = end - timedelta(days=days)
        
        url = (
            f"https://www.nseindia.com/api/historical/cm/equity"
            f"?symbol={symbol}"
            f"&series=[%22EQ%22]"
            f"&from={start.strftime('%d-%m-%Y')}"
            f"&to={end.strftime('%d-%m-%Y')}"
            f"&csv=false"
        )
        resp = session.get(url, headers=NSE_HEADERS, timeout=15)
        if resp.status_code != 200:
            return None
        
        data = resp.json().get('data', [])
        if not data or len(data) < 50:
            return None
        
        rows = []
        for d in data:
            try:
                rows.append({
                    'Date': pd.to_datetime(d['CH_TIMESTAMP']),
                    'Open':   float(str(d['CH_OPENING_PRICE']).replace(',','')),
                    'High':   float(str(d['CH_TRADE_HIGH_PRICE']).replace(',','')),
                    'Low':    float(str(d['CH_TRADE_LOW_PRICE']).replace(',','')),
                    'Close':  float(str(d['CH_CLOSING_PRICE']).replace(',','')),
                    'Volume': float(str(d['CH_TOT_TRADED_QTY']).replace(',','')),
                })
            except:
                continue
        
        if not rows:
            return None
            
        df = pd.DataFrame(rows).set_index('Date').sort_index()
        df = df[~df.index.duplicated(keep='last')]
        print(f"NSE {symbol}: {len(df)} candles, ₹{df['Close'].iloc[-1]:,.2f}")
        return df
    except Exception as e:
        print(f"NSE fetch failed for {symbol}: {e}")
        return None
