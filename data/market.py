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

def fetch_coingecko_ohlcv(ticker: str, days: int = 365) -> pd.DataFrame | None:
    """Fetch OHLCV from CoinGecko — works on Railway, no geo-block."""
    cg_id = COINGECKO_ID_MAP.get(ticker)
    if not cg_id:
        return None
    try:
        # OHLC endpoint — returns [timestamp, open, high, low, close]
        url = f"https://api.coingecko.com/api/v3/coins/{cg_id}/ohlc?vs_currency=usd&days={days}"
        resp = requests.get(url, timeout=15)
        data = resp.json()

        if not data or not isinstance(data, list):
            return None

        df = pd.DataFrame(data, columns=["timestamp", "Open", "High", "Low", "Close"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        df = df.set_index("timestamp")
        df.index.name = None
        df = df.astype(float)

        # Add Volume column (CoinGecko OHLC doesn't include volume — fetch separately)
        try:
            vol_url = f"https://api.coingecko.com/api/v3/coins/{cg_id}/market_chart?vs_currency=usd&days={days}&interval=daily"
            vol_resp = requests.get(vol_url, timeout=15).json()
            volumes = vol_resp.get("total_volumes", [])
            if volumes:
                vol_df = pd.DataFrame(volumes, columns=["timestamp", "Volume"])
                vol_df["timestamp"] = pd.to_datetime(vol_df["timestamp"], unit="ms").dt.normalize()
                vol_df = vol_df.set_index("timestamp")
                df.index = df.index.normalize()
                df = df.join(vol_df, how="left")
            else:
                df["Volume"] = 0.0
        except Exception:
            df["Volume"] = 0.0

        # Remove duplicate index entries
        df = df[~df.index.duplicated(keep="last")]
        df = df.sort_index()

        latest = float(df["Close"].iloc[-1])
        print(f"CoinGecko OHLCV for {ticker}: {len(df)} candles, latest close ${latest:,.2f}")
        return df

    except Exception as e:
        print(f"CoinGecko OHLCV failed for {ticker}: {e}")
        return None

def fetch_ohlcv(ticker: str, period: str = "2y") -> pd.DataFrame | None:
    # Use CoinGecko for all crypto
    if ticker in COINGECKO_ID_MAP:
        days = 730 if period == "2y" else 365
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
