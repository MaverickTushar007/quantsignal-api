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

def fetch_coingecko_ohlcv(ticker: str, days: int = 180) -> pd.DataFrame | None:
    """Fetch OHLCV from CoinGecko — works on Railway, no geo-block."""
    cg_id = COINGECKO_ID_MAP.get(ticker)
    if not cg_id:
        return None
    try:
        url = f"https://api.coingecko.com/api/v3/coins/{cg_id}/ohlc?vs_currency=usd&days={days}"
        resp = requests.get(url, timeout=20)
        data = resp.json()

        if not data or not isinstance(data, list) or len(data) < 10:
            print(f"CoinGecko bad response for {ticker}: {str(data)[:100]}")
            return None

        rows = []
        for item in data:
            try:
                ts = pd.Timestamp(item[0], unit="ms")
                rows.append({
                    "Open": float(item[1]),
                    "High": float(item[2]),
                    "Low": float(item[3]),
                    "Close": float(item[4]),
                    "Volume": 1000000.0,
                })
            except Exception:
                continue

        if len(rows) < 10:
            return None

        df = pd.DataFrame(rows)
        timestamps = [pd.Timestamp(item[0], unit="ms") for item in data[:len(rows)]]
        df.index = pd.DatetimeIndex(timestamps)
        df = df[~df.index.duplicated(keep="last")]
        df = df.sort_index()

        print(f"CoinGecko OHLCV for {ticker}: {len(df)} candles, latest ${df['Close'].iloc[-1]:,.2f}")
        return df

    except Exception as e:
        print(f"CoinGecko OHLCV failed for {ticker}: {e}")
        return None

def fetch_ohlcv(ticker: str, period: str = "2y") -> pd.DataFrame | None:
    # Use CoinGecko for all crypto
    if ticker in COINGECKO_ID_MAP:
        days = 180  # keep it fast on Railway
        df = fetch_coingecko_ohlcv(ticker, days=days)
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
