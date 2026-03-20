import yfinance as yf
import pandas as pd
import requests
import time

BINANCE_SYMBOL_MAP = {
    "BTC-USD": "BTCUSDT", "ETH-USD": "ETHUSDT", "SOL-USD": "SOLUSDT",
    "BNB-USD": "BNBUSDT", "XRP-USD": "XRPUSDT", "DOGE-USD": "DOGEUSDT",
    "ADA-USD": "ADAUSDT", "AVAX-USD": "AVAXUSDT", "MATIC-USD": "MATICUSDT",
    "DOT-USD": "DOTUSDT", "LINK-USD": "LINKUSDT", "LTC-USD": "LTCUSDT",
    "ATOM-USD": "ATOMUSDT", "NEAR-USD": "NEARUSDT", "OP-USD": "OPUSDT",
    "INJ-USD": "INJUSDT", "FET-USD": "FETUSDT", "PEPE-USD": "PEPEUSDT",
}

def fetch_binance_ohlcv(symbol: str, period_days: int = 730) -> pd.DataFrame | None:
    """Fetch OHLCV from Binance spot API — never blocked, always live."""
    binance_symbol = BINANCE_SYMBOL_MAP.get(symbol)
    if not binance_symbol:
        return None
    try:
        # Binance klines endpoint — 1000 daily candles max per request
        url = "https://api.binance.com/api/v3/klines"
        params = {
            "symbol": binance_symbol,
            "interval": "1d",
            "limit": min(period_days, 1000),
        }
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()
        if not data or isinstance(data, dict):
            return None

        df = pd.DataFrame(data, columns=[
            "Open time", "Open", "High", "Low", "Close", "Volume",
            "Close time", "Quote volume", "Trades", "Taker buy base",
            "Taker buy quote", "Ignore"
        ])
        df["Open time"] = pd.to_datetime(df["Open time"], unit="ms")
        df = df.set_index("Open time")
        df = df[["Open", "High", "Low", "Close", "Volume"]].astype(float)
        df.index.name = None

        print(f"Binance OHLCV fetched for {symbol}: {len(df)} candles, latest close ${float(df['Close'].iloc[-1]):,.2f}")
        return df
    except Exception as e:
        print(f"Binance OHLCV failed for {symbol}: {e}")
        return None

def fetch_ohlcv(ticker: str, period: str = "2y") -> pd.DataFrame | None:
    # Try Binance first for crypto
    if ticker in BINANCE_SYMBOL_MAP:
        df = fetch_binance_ohlcv(ticker)
        if df is not None and len(df) > 50:
            return df

    # Fall back to yFinance for stocks, ETFs, forex
    for attempt in range(3):
        try:
            t = yf.Ticker(ticker)
            df = t.history(period=period, auto_adjust=True)
            if df is not None and len(df) > 50:
                df.index = df.index.tz_localize(None) if df.index.tzinfo else df.index
                return df
        except Exception as e:
            print(f"yFinance attempt {attempt+1} failed for {ticker}: {e}")
            time.sleep(2)
    return None
