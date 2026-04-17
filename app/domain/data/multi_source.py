"""
Multi-source OHLCV fetcher.
Priority: yfinance → CoinGecko (crypto) → Alpha Vantage (stocks) → stooq
Never fails silently — logs which source succeeded.
"""
import logging, os, time
import pandas as pd
log = logging.getLogger(__name__)

# ── Source 0: Yahoo Finance Direct (no yfinance, no rate limits) ──────────
def _fetch_yahoo_direct(symbol: str, period: str = "2y"):
    try:
        import requests, pandas as pd
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Accept": "application/json",
        }
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range={period}"
        r = requests.get(url, headers=headers, timeout=15)
        data = r.json()
        result = data.get("chart", {}).get("result", [])
        if not result:
            return None
        timestamps = result[0].get("timestamp", [])
        q = result[0]["indicators"]["quote"][0]
        df = pd.DataFrame({
            "Open": q.get("open"), "High": q.get("high"), "Low": q.get("low"),
            "Close": q.get("close"), "Volume": q.get("volume"),
        }, index=pd.to_datetime(timestamps, unit="s"))
        df = df.dropna(subset=["Close"])
        df.index = df.index.tz_localize(None) if df.index.tzinfo else df.index
        if len(df) > 50:
            log.info(f"[multi_source] yahoo_direct OK for {symbol}: {len(df)} rows")
            return df
    except Exception as e:
        log.debug(f"[multi_source] yahoo_direct failed for {symbol}: {e}")
    return None

# ── Source 1: yfinance ─────────────────────────────────────────────────────
def _fetch_yfinance(symbol: str, period: str = "2y"):
    try:
        import yfinance as yf
        t = yf.Ticker(symbol)
        df = t.history(period=period, auto_adjust=True)
        if df is not None and len(df) > 50:
            df.index = df.index.tz_localize(None) if df.index.tzinfo else df.index
            log.info(f"[multi_source] yfinance OK for {symbol}: {len(df)} rows")
            return df
    except Exception as e:
        log.debug(f"[multi_source] yfinance failed for {symbol}: {e}")
    return None

# ── Source 2: stooq (free, no API key, good for stocks+indices) ───────────
def _fetch_stooq(symbol: str):
    """
    stooq works for US stocks, indices, and some international.
    Symbol mapping: AAPL → AAPL.US, ^GSPC → ^SPX, RELIANCE.NS → RELIANCE.IN
    """
    try:
        import pandas_datareader as pdr
        from datetime import datetime, timedelta
        # Map symbol to stooq format
        stooq_sym = _to_stooq_symbol(symbol)
        if not stooq_sym:
            return None
        end = datetime.now()
        start = end - timedelta(days=730)
        df = pdr.get_data_stooq(stooq_sym, start=start, end=end)
        if df is not None and len(df) > 50:
            df = df.sort_index()
            df.columns = [c.title() for c in df.columns]
            log.info(f"[multi_source] stooq OK for {symbol} ({stooq_sym}): {len(df)} rows")
            return df
    except Exception as e:
        log.debug(f"[multi_source] stooq failed for {symbol}: {e}")
    return None

def _to_stooq_symbol(symbol: str) -> str:
    """Convert symbol to stooq format."""
    mapping = {
        "^GSPC": "^SPX", "^DJI": "^DJI", "^IXIC": "^NDQ",
        "^VIX": "^VIX", "GC=F": "GC.F", "CL=F": "CL.F",
        "EURUSD=X": "EUR/USD", "GBPUSD=X": "GBP/USD",
        "USDINR=X": "USD/INR", "DX-Y.NYB": "^USD",
    }
    if symbol in mapping:
        return mapping[symbol]
    if symbol.endswith(".NS"):
        return symbol.replace(".NS", ".IN")
    if symbol.endswith("-USD") or "-" in symbol:
        return None  # stooq doesn't do crypto well
    if not any(c in symbol for c in [".", "=", "^", "-"]):
        return f"{symbol}.US"
    return None

# ── Source 3: Alpha Vantage (free tier, 25 req/day) ───────────────────────
def _fetch_alpha_vantage(symbol: str):
    try:
        api_key = os.environ.get("ALPHA_VANTAGE_KEY", "")
        if not api_key:
            return None
        import requests
        # Clean symbol for AV
        av_sym = symbol.replace(".NS", ".BSE").replace("=X", "").replace("^", "")
        url = f"https://www.alphavantage.co/query?function=TIME_SERIES_DAILY_ADJUSTED&symbol={av_sym}&outputsize=full&apikey={api_key}"
        r = requests.get(url, timeout=10)
        data = r.json()
        ts = data.get("Time Series (Daily)", {})
        if not ts:
            return None
        rows = []
        for date, vals in sorted(ts.items()):
            rows.append({
                "Date": pd.Timestamp(date),
                "Open":   float(vals["1. open"]),
                "High":   float(vals["2. high"]),
                "Low":    float(vals["3. low"]),
                "Close":  float(vals["5. adjusted close"]),
                "Volume": float(vals["6. volume"]),
            })
        df = pd.DataFrame(rows).set_index("Date")
        if len(df) > 50:
            log.info(f"[multi_source] alpha_vantage OK for {symbol}: {len(df)} rows")
            return df
    except Exception as e:
        log.debug(f"[multi_source] alpha_vantage failed for {symbol}: {e}")
    return None

# ── Source 4: CoinGecko for crypto ────────────────────────────────────────
def _fetch_coingecko(symbol: str):
    try:
        from app.domain.data.market import COINGECKO_ID_MAP, fetch_coingecko_ohlcv
        if symbol not in COINGECKO_ID_MAP:
            return None
        df = fetch_coingecko_ohlcv(symbol, days=365)
        if df is not None and len(df) > 50:
            log.info(f"[multi_source] coingecko OK for {symbol}: {len(df)} rows")
            return df
    except Exception as e:
        log.debug(f"[multi_source] coingecko failed for {symbol}: {e}")
    return None

# ── Master fetcher ─────────────────────────────────────────────────────────
def fetch_ohlcv_multi(symbol: str, period: str = "2y"):
    """
    Try all sources in order. Return first successful result.
    """
    # Crypto: CoinGecko first
    if symbol.endswith("-USD") or symbol in ["BTC-USD","ETH-USD","SOL-USD"]:
        df = _fetch_coingecko(symbol)
        if df is not None:
            return df

    # Try yfinance first
    df = _fetch_yfinance(symbol, period)
    if df is not None:
        return df

    # Try stooq
    df = _fetch_stooq(symbol)
    if df is not None:
        return df

    # Try Alpha Vantage (only if key set)
    df = _fetch_alpha_vantage(symbol)
    if df is not None:
        return df

    log.warning(f"[multi_source] ALL sources failed for {symbol}")
    return None


def validate_ohlcv(df, symbol: str) -> tuple:
    """
    Sanity-check a DataFrame before feeding it to the ML model.
    Returns (is_valid: bool, warnings: list[str])
    """
    warnings = []
    if df is None or len(df) < 50:
        return False, ["Insufficient data — fewer than 50 rows"]

    from datetime import datetime, timedelta
    last_date = df.index[-1]
    if hasattr(last_date, 'to_pydatetime'):
        last_date = last_date.to_pydatetime().replace(tzinfo=None)
    staleness_days = (datetime.utcnow() - last_date).days

    # Staleness check — data older than 5 days is suspect
    if staleness_days > 5:
        warnings.append(f"Stale data — last close is {staleness_days} days old")

    # Price sanity — last close must be positive and not an outlier
    closes = df["Close"].dropna()
    if len(closes) < 10:
        return False, ["Too few valid close prices"]

    last_close = float(closes.iloc[-1])
    median_close = float(closes.median())

    if last_close <= 0:
        return False, ["Invalid close price — zero or negative"]

    # Flag if last close deviates >50% from median (possible bad tick)
    if median_close > 0 and abs(last_close - median_close) / median_close > 0.50:
        warnings.append(f"Price anomaly — last close {last_close:.2f} deviates >50% from median {median_close:.2f}")

    # Volume sanity — last volume shouldn't be zero on a trading day
    try:
        last_vol = float(df["Volume"].iloc[-1])
        if last_vol == 0 and staleness_days <= 1:
            warnings.append("Zero volume on latest bar — possible data feed issue")
    except Exception:
        pass

    return True, warnings
