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
}

def get_live_crypto_price(ticker: str) -> float | None:
    """Get live price from CoinGecko — no geo restrictions."""
    cg_id = COINGECKO_ID_MAP.get(ticker)
    if not cg_id:
        return None
    try:
        url = f"https://api.coingecko.com/api/v3/simple/price?ids={cg_id}&vs_currencies=usd"
        resp = requests.get(url, timeout=8)
        data = resp.json()
        price = data.get(cg_id, {}).get("usd")
        if price:
            print(f"CoinGecko live price for {ticker}: ${price:,.2f}")
            return float(price)
    except Exception as e:
        print(f"CoinGecko price failed for {ticker}: {e}")
    return None

def fetch_ohlcv(ticker: str, period: str = "2y") -> pd.DataFrame | None:
    # Use yFinance for OHLCV history (works on Railway for crypto history)
    for attempt in range(3):
        try:
            t = yf.Ticker(ticker)
            df = t.history(period=period, auto_adjust=True)
            if df is not None and len(df) > 50:
                df.index = df.index.tz_localize(None) if df.index.tzinfo else df.index

                # Patch the latest close with CoinGecko live price for crypto
                live_price = get_live_crypto_price(ticker)
                if live_price:
                    df.iloc[-1, df.columns.get_loc("Close")] = live_price
                    print(f"Patched {ticker} latest close to live price: ${live_price:,.2f}")

                return df
        except Exception as e:
            print(f"yFinance attempt {attempt+1} failed for {ticker}: {e}")
            time.sleep(2)
    return None
