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

def fetch_coingecko_ohlcv(ticker, days=730):
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

def fetch_ohlcv(ticker, period="5y"):
    # Try Yahoo direct first — most reliable, full history, no rate limits
    try:
        from app.domain.data.multi_source import _fetch_yahoo_direct
        df = _fetch_yahoo_direct(ticker, period)
        if df is not None and len(df) > 200:
            return df
    except Exception as e:
        print(f"Yahoo direct failed for {ticker}: {e}")
    # yfinance fallback (covers crypto + equities)
    try:
        t = yf.Ticker(ticker)
        df = t.history(period=period, auto_adjust=True)
        if df is not None and len(df) > 200:
            df.index = df.index.tz_localize(None) if df.index.tzinfo else df.index
            return df
    except Exception as e:
        print(f"yFinance fallback 1 failed for {ticker}: {e}")
    # CoinGecko last resort (only 23 days on free tier IPs — unreliable)
    if ticker in COINGECKO_ID_MAP:
        days = 730
        df = fetch_coingecko_ohlcv(ticker, days=days)
        if df is not None and len(df) > 200:
            return df

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
        df = fetch_ohlcv_multi(ticker, period)
        if df is not None:
            return df
    except Exception as e:
        print(f"Multi-source fallback failed for {ticker}: {e}")

    return None

def fetch_ohlcv_as_of(ticker: str, as_of, lookback_days: int = 500):
    """Fetch OHLCV trimmed to as_of date — for historical backtests."""
    import pandas as pd
    import yfinance as yf
    from datetime import datetime as _dt, timedelta as _td
    if isinstance(as_of, str):
        as_of = _dt.fromisoformat(as_of)
    start_dt = as_of - _td(days=lookback_days)
    end_dt   = as_of + _td(days=1)
    try:
        t = yf.Ticker(ticker)
        df = t.history(start=start_dt.strftime("%Y-%m-%d"),
                       end=end_dt.strftime("%Y-%m-%d"),
                       auto_adjust=True)
        if df is not None and len(df) > 50:
            df.index = df.index.tz_localize(None) if df.index.tzinfo else df.index
            return df
    except Exception as e:
        print(f"fetch_ohlcv_as_of yf failed for {ticker}: {e}")
    for period in ("2y", "5y", "max"):
        try:
            df = fetch_ohlcv(ticker, period=period)
            if df is None or df.empty:
                continue
            if df.index.tzinfo is not None:
                df.index = df.index.tz_convert(None)
            cutoff = pd.Timestamp(as_of).normalize()
            df = df[df.index <= cutoff]
            if len(df) > 50:
                return df
        except Exception:
            pass
    return None
