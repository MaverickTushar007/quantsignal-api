import yfinance as yf
import pandas as pd
import requests
import time

COINGECKO_ID_MAP = {
    "BTC-USD": "bitcoin", "ETH-USD": "ethereum", "SOL-USD": "solana",
    "BNB-USD": "binancecoin", "XRP-USD": "ripple", "DOGE-USD": "dogecoin",
    "ADA-USD": "cardano", "AVAX-USD": "avalanche-2", "MATIC-USD": "matic-network",
    "DOT-USD": "polkadot", "LINK-USD": "chainlink", "LTC-USD": "litecoin",
    "ATOM-USD": "cosmos", "NEAR-USD": "near", "OP-USD": "optimism",
    "INJ-USD": "injective-protocol", "FET-USD": "fetch-ai",
    "PEPE-USD": "pepe",
}

def fetch_coingecko_ohlcv(ticker, days=365):
    cg_id = COINGECKO_ID_MAP.get(ticker)
    if not cg_id:
        return None
    try:
        url = f"https://api.coingecko.com/api/v3/coins/{cg_id}/market_chart?vs_currency=usd&days={days}&interval=daily"
        resp = requests.get(url, timeout=20)
        data = resp.json()
        prices = data.get("prices", [])
        volumes = data.get("total_volumes", [])
        if not prices or len(prices) < 50:
            print(f"CoinGecko insufficient data for {ticker}: {len(prices)} points")
            return None
        rows = []
        timestamps = []
        for i, item in enumerate(prices):
            ts = pd.Timestamp(item[0], unit="ms").normalize()
            close = float(item[1])
            vol = float(volumes[i][1]) if i < len(volumes) else 1000000.0
            rows.append({"Open": close, "High": close * 1.005, "Low": close * 0.995, "Close": close, "Volume": vol})
            timestamps.append(ts)
        df = pd.DataFrame(rows, index=pd.DatetimeIndex(timestamps))
        df = df[~df.index.duplicated(keep="last")]
        df = df.sort_index()
        print(f"CoinGecko market_chart for {ticker}: {len(df)} candles, latest ${df['Close'].iloc[-1]:,.2f}")
        return df
    except Exception as e:
        print(f"CoinGecko failed for {ticker}: {e}")
        return None

_FETCH_SOURCE = {}  # module-level registry: symbol -> last source used

def fetch_ohlcv(ticker, period="2y"):
    if ticker in COINGECKO_ID_MAP:
        days = 180
        df = fetch_coingecko_ohlcv(ticker, days=days)
        if df is not None and len(df) > 50:
            return df
    # Try Yahoo direct first — fastest, no rate limits, works for all symbols
    try:
        from app.domain.data.multi_source import _fetch_yahoo_direct
        _FETCH_SOURCE[ticker] = "yahoo_direct"
        df = _fetch_yahoo_direct(ticker, period)
        if df is not None:
            return df
    except Exception as e:
        print(f"Yahoo direct failed for {ticker}: {e}")

    # yfinance fallback
    try:
        t = yf.Ticker(ticker)
        df = t.history(period=period, auto_adjust=True)
        if df is not None and len(df) > 50:
            df.index = df.index.tz_localize(None) if df.index.tzinfo else df.index
            return df
    except Exception as e:
        print(f"yFinance failed for {ticker}: {e}")

    # Full multi-source fallback
    try:
        from app.domain.data.multi_source import fetch_ohlcv_multi
        _FETCH_SOURCE[ticker] = "multi_source"
        df = fetch_ohlcv_multi(ticker, period)
        if df is not None:
            return df
    except Exception as e:
        print(f"Multi-source fallback failed for {ticker}: {e}")

    return None
