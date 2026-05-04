from __future__ import annotations
import json, time, urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

BASE_DIR  = Path(__file__).resolve().parents[3]
COT_CACHE = BASE_DIR / "data" / "cot_cache.json"
COT_CACHE.parent.mkdir(parents=True, exist_ok=True)

# Primary: CFTC direct (works locally, often blocked on cloud IPs)
CFTC_CURRENT_URL = "https://www.cftc.gov/dea/newcot/c_disagg.txt"
CFTC_FINANCIAL_URL = "https://www.cftc.gov/dea/newcot/FinFutWk.txt"
CFTC_FINANCIAL_URL = "https://www.cftc.gov/dea/newcot/FinFutWk.txt"
# Fallback mirrors for cloud deployment (Railway/Render/etc)
CFTC_MIRRORS = [
    "https://www.cftc.gov/dea/newcot/c_disagg.txt",
    "https://www.cftc.gov/dea/newcot/f_disagg.txt",
]

COT_SYMBOL_MAP = {
    # Forex — FinFutWk.txt (uppercased by parser)
    "EURUSD=X": "EURO FX - CHICAGO MERCANTILE EXCHANGE",
    "GBPUSD=X": "BRITISH POUND - CHICAGO MERCANTILE EXCHANGE",
    "JPYUSD=X": "JAPANESE YEN - CHICAGO MERCANTILE EXCHANGE",
    "AUDUSD=X": "AUSTRALIAN DOLLAR - CHICAGO MERCANTILE EXCHANGE",
    "CHFUSD=X": "SWISS FRANC - CHICAGO MERCANTILE EXCHANGE",
    # Commodities — c_disagg.txt (uppercased by parser)
    "GC=F":     "GOLD - COMMODITY EXCHANGE INC.",
    "SI=F":     "SILVER - COMMODITY EXCHANGE INC.",
    "CL=F":     "CRUDE OIL, LIGHT SWEET-WTI - ICE FUTURES EUROPE",
    "NG=F":     "NAT GAS NYME - NEW YORK MERCANTILE EXCHANGE",
    "HG=F":     "COPPER- #1 - COMMODITY EXCHANGE INC.",
    # Equity index — FinFutWk.txt (uppercased)
    "^GSPC":    "S&P 500 CONSOLIDATED - CHICAGO MERCANTILE EXCHANGE",
    "^NDX":     "NASDAQ-100 CONSOLIDATED - CHICAGO MERCANTILE EXCHANGE",
    "^DJIA":    "DJIA CONSOLIDATED - CHICAGO BOARD OF TRADE",
    # Crypto — FinFutWk.txt
    "BTC-USD":  "BITCOIN - CHICAGO MERCANTILE EXCHANGE",
    "ETH-USD":  "ETHER CASH SETTLED - CHICAGO MERCANTILE EXCHANGE",
    "SOL-USD":  "SOL - CHICAGO MERCANTILE EXCHANGE",
    "XRP-USD":  "XRP - CHICAGO MERCANTILE EXCHANGE",
}

# Which file each symbol lives in
_COT_FILE_MAP = {
    "EURUSD=X": "financial", "GBPUSD=X": "financial", "JPYUSD=X": "financial",
    "CADUSD=X": "financial", "AUDUSD=X": "financial", "CHFUSD=X": "financial",
    "^GSPC": "financial", "^NDX": "financial", "^DJIA": "financial",
    "BTC-USD": "financial",
    "GC=F": "commodity", "SI=F": "commodity", "CL=F": "commodity",
    "NG=F": "commodity", "HG=F": "commodity",
}

def _fetch_cot_url(url: str) -> Optional[str]:
    """Fetch a single CFTC URL with fallback headers."""
    import requests as _req
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept": "text/plain, */*",
    }
    try:
        resp = _req.get(url, headers=headers, timeout=20)
        if resp.status_code == 200 and len(resp.text) > 500:
            return resp.text
    except Exception as e:
        print(f"[cot] {url} failed: {e}")
    return None


def _fetch_cot_url(url: str) -> Optional[str]:
    """Fetch a single CFTC URL with fallback headers."""
    import requests as _req
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept": "text/plain, */*",
    }
    try:
        resp = _req.get(url, headers=headers, timeout=20)
        if resp.status_code == 200 and len(resp.text) > 500:
            return resp.text
    except Exception as e:
        print(f"[cot] {url} failed: {e}")
    return None


def _fetch_cot_raw() -> Optional[str]:
    """
    Fetch COT report. Tries multiple strategies to work around Railway IP blocks.
    Falls back to a pre-parsed static snapshot if all live fetches fail.
    """
    import requests as _req
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept": "text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
    }
    urls = [
        "https://www.cftc.gov/dea/newcot/c_disagg.txt",
        "https://www.cftc.gov/files/dea/history/com_disagg_txt_2025.zip",
    ]
    for url in urls[:1]:  # try CFTC direct
        try:
            resp = _req.get(url, headers=headers, timeout=20)
            if resp.status_code == 200 and len(resp.text) > 500:
                print(f"[cot] fetched from CFTC: {len(resp.text)} chars")
                return resp.text
            print(f"[cot] CFTC returned {resp.status_code}")
        except Exception as e:
            print(f"[cot] CFTC failed: {e}")

    # Try Quandl-style CSV via datahub.io (public mirror)
    try:
        alt = "https://datahub.io/core/cot-reports/r/c_disagg.csv"
        resp = _req.get(alt, headers=headers, timeout=15)
        if resp.status_code == 200 and len(resp.text) > 500:
            print("[cot] fetched from datahub mirror")
            return resp.text
    except Exception as e:
        print(f"[cot] datahub failed: {e}")

    print("[cot] all fetch attempts failed — using cached data if available")
    return None


def _parse_cot_csv(raw: str) -> dict:
    results = {}
    lines = raw.strip().split("\n")
    for line in lines[1:]:
        try:
            fields, in_quote, current = [], False, []
            for ch in line:
                if ch == '"':
                    in_quote = not in_quote
                elif ch == "," and not in_quote:
                    fields.append("".join(current).strip())
                    current = []
                else:
                    current.append(ch)
            fields.append("".join(current).strip())
            if len(fields) < 20:
                continue
            market_name   = fields[0].upper().strip()
            report_date   = fields[2].strip()
            open_interest = float(fields[7].replace(",", "") or 0)
            nc_long       = float(fields[13].replace(",", "") or 0)
            nc_short      = float(fields[14].replace(",", "") or 0)
            if open_interest == 0:
                continue
            net_position = nc_long - nc_short
            cot_score    = net_position / open_interest
            if   cot_score >  0.40: signal = "CROWDED_LONG"
            elif cot_score >  0.20: signal = "NET_LONG"
            elif cot_score < -0.40: signal = "CROWDED_SHORT"
            elif cot_score < -0.20: signal = "NET_SHORT"
            else:                   signal = "NEUTRAL"
            results[market_name] = {
                "report_date": report_date, "open_interest": open_interest,
                "nc_long": nc_long, "nc_short": nc_short,
                "net_position": net_position, "cot_score": round(cot_score, 4),
                "signal": signal,
            }
        except Exception:
            continue
    return results

def get_cot_signal(symbol: str, max_cache_hours: int = 168) -> dict:
    market_name = COT_SYMBOL_MAP.get(symbol)
    if not market_name:
        return {"signal": "NEUTRAL", "cot_score": 0.0, "source": "CFTC", "available": False}
    cache = _load_cache()
    cache_key = f"cot::{market_name}"
    if cache_key in cache:
        cached = cache[cache_key]
        fetched_at = datetime.fromisoformat(cached.get("fetched_at", "2000-01-01"))
        if (datetime.utcnow() - fetched_at.replace(tzinfo=None)) < timedelta(hours=max_cache_hours):
            return cached
    raw = _fetch_cot_raw()
    if raw is None:
        return cache.get(cache_key, {"signal": "NEUTRAL", "cot_score": 0.0, "source": "CFTC"})
    parsed = _parse_cot_csv(raw)
    for name, data in parsed.items():
        data["fetched_at"] = datetime.utcnow().isoformat()
        data["source"]     = "CFTC"
        data["available"]  = True
        cache[f"cot::{name}"] = data
    _save_cache(cache)
    result = parsed.get(market_name, {"signal": "NEUTRAL", "cot_score": 0.0, "source": "CFTC", "available": False})
    result["fetched_at"] = datetime.utcnow().isoformat()
    return result

def get_all_cot_signals() -> dict:
    return {sym: get_cot_signal(sym) for sym in COT_SYMBOL_MAP if get_cot_signal(sym).get("available")}

def cot_confluence_score(symbol: str, direction: str) -> float:
    sig = get_cot_signal(symbol).get("signal", "NEUTRAL")
    if direction == "BUY":  return 1.0 if sig in ("NET_LONG",  "CROWDED_SHORT") else 0.0
    if direction == "SELL": return 1.0 if sig in ("NET_SHORT", "CROWDED_LONG")  else 0.0
    return 0.0

def _load_cache() -> dict:
    if COT_CACHE.exists():
        try:
            with open(COT_CACHE) as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def _save_cache(cache: dict):
    with open(COT_CACHE, "w") as f:
        json.dump(cache, f, indent=2)
