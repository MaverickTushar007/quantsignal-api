from __future__ import annotations
import json, time, urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

BASE_DIR  = Path(__file__).resolve().parents[3]
COT_CACHE = BASE_DIR / "data" / "cot_cache.json"
COT_CACHE.parent.mkdir(parents=True, exist_ok=True)

# Primary: CFTC direct (works locally, often blocked on cloud IPs)
CFTC_CURRENT_URL = "https://www.cftc.gov/dea/newcot/c_disagg.txt"
# Fallback mirrors for cloud deployment (Railway/Render/etc)
CFTC_MIRRORS = [
    "https://raw.githubusercontent.com/datasets/cot-reports/main/data/c_disagg.txt",
    "https://www.cftc.gov/dea/newcot/c_disagg.txt",
]

COT_SYMBOL_MAP = {
    "EURUSD=X": "EURO FX",
    "GBPUSD=X": "BRITISH POUND STERLING",
    "JPYUSD=X": "JAPANESE YEN",
    "CADUSD=X": "CANADIAN DOLLAR",
    "AUDUSD=X": "AUSTRALIAN DOLLAR",
    "CHFUSD=X": "SWISS FRANC",
    "GC=F":     "GOLD",
    "SI=F":     "SILVER",
    "CL=F":     "CRUDE OIL, LIGHT SWEET",
    "NG=F":     "NATURAL GAS",
    "HG=F":     "COPPER-GRADE #1",
    "^GSPC":    "S&P 500 STOCK INDEX",
    "^NDX":     "NASDAQ MINI",
    "^DJIA":    "DJIA CONSOLIDATED",
    "BTC-USD":  "BITCOIN",
}

def _fetch_cot_raw() -> Optional[str]:
    """Try primary CFTC URL then mirrors — handles Railway IP blocks."""
    urls = [CFTC_CURRENT_URL] + [m for m in CFTC_MIRRORS if m != CFTC_CURRENT_URL]
    for url in urls:
        try:
            req = urllib.request.Request(url,
                headers={"User-Agent": "QuantSignal/1.0 (research)"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read().decode("latin-1")
                if len(raw) > 1000:   # sanity check — real file is ~500KB
                    print(f"[cot] fetched from {url}")
                    return raw
        except Exception as e:
            print(f"[cot] {url} failed: {e}")
            continue
    # Last resort: try requests with different headers
    try:
        import requests as _req
        resp = _req.get(CFTC_CURRENT_URL, timeout=15,
            headers={"User-Agent": "Mozilla/5.0", "Accept": "text/plain"})
        if resp.status_code == 200 and len(resp.text) > 1000:
            print("[cot] fetched via requests fallback")
            return resp.text
    except Exception as e:
        print(f"[cot] requests fallback failed: {e}")
    print("[cot] all COT fetch attempts failed")
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
        if datetime.utcnow() - fetched_at < timedelta(hours=max_cache_hours):
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
