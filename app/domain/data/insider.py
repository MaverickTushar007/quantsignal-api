"""
data/insider.py
SEC EDGAR Form 4 insider trade feed for US stocks.
Returns recent insider buys/sells for a given ticker.
"""
import urllib.request, json
from datetime import datetime, timezone, timedelta
from functools import lru_cache

EDGAR_HEADERS = {"User-Agent": "quantsignal contact@quantsignal.app"}

# Map ticker -> CIK (cache on first lookup)
_CIK_CACHE = {}

def _get_cik(ticker: str) -> str | None:
    if ticker in _CIK_CACHE:
        return _CIK_CACHE[ticker]
    try:
        url = f"https://efts.sec.gov/LATEST/search-index?q=%22{ticker}%22&forms=4&dateRange=custom&startdt=2025-01-01"
        req = urllib.request.Request(url, headers=EDGAR_HEADERS)
        data = json.loads(urllib.request.urlopen(req, timeout=8).read())
        hits = data.get("hits", {}).get("hits", [])
        for hit in hits:
            names = hit.get("_source", {}).get("display_names", [])
            for name in names:
                if f"({ticker})" in name:
                    import re
                    m = re.search(r"CIK (\d+)", name)
                    if m:
                        cik = m.group(1).lstrip("0")
                        _CIK_CACHE[ticker] = cik
                        return cik
    except Exception:
        pass
    return None

# Known CIK map for common tickers
TICKER_CIK = {
    "AAPL": "0000320193", "NVDA": "0001045810", "MSFT": "0000789019",
    "GOOGL": "0001652044", "AMZN": "0001018724", "META": "0001326801",
    "TSLA": "0001318605", "JPM": "0000019617", "BAC": "0000070858",
    "WMT": "0000104169", "SPY": None, "QQQ": None,
}

TICKER_COMPANY = {
    "NVDA": "NVIDIA", "AAPL": "APPLE", "MSFT": "MICROSOFT",
    "GOOGL": "ALPHABET", "AMZN": "AMAZON", "META": "META PLATFORMS",
    "TSLA": "TESLA", "JPM": "JPMORGAN", "BAC": "BANK OF AMERICA",
    "WMT": "WALMART", "AMD": "ADVANCED MICRO", "INTC": "INTEL",
}

def get_insider_trades(ticker: str, days: int = 30) -> dict:
    """Get recent insider Form 4 trades for a US stock ticker."""
    if ticker.endswith(".NS") or ticker.endswith(".BO") or ticker.endswith("-USD"):
        return {"available": False, "reason": "India/crypto — SEC not applicable"}

    try:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        clean = ticker.replace("^", "").replace("=X", "").split("-")[0]

        # Get CIK
        cik = TICKER_CIK.get(clean)
        if cik is None and clean not in TICKER_CIK:
            # Try to look it up
            cik = _get_cik(clean)
        if not cik:
            return {"available": False, "reason": "CIK not found"}

        # Fetch filings by CIK directly
        cik_num = cik.lstrip("0")
        url = (f"https://data.sec.gov/submissions/CIK{cik.zfill(10)}.json")
        req = urllib.request.Request(url, headers=EDGAR_HEADERS)
        data = json.loads(urllib.request.urlopen(req, timeout=10).read())

        company_name = data.get("name", clean)
        recent = data.get("filings", {}).get("recent", {})
        forms     = recent.get("form", [])
        dates     = recent.get("filingDate", [])
        reporters = recent.get("reportingOwner", []) if "reportingOwner" in recent else []

        # Get filer names from EDGAR search (has display_names)
        company_q = TICKER_COMPANY.get(clean, clean)
        search_url = (f"https://efts.sec.gov/LATEST/search-index?q=%22{company_q}%22"
                      f"&forms=4&dateRange=custom&startdt={since}&enddt={today}")
        try:
            sreq = urllib.request.Request(search_url, headers=EDGAR_HEADERS)
            sdata = json.loads(urllib.request.urlopen(sreq, timeout=8).read())
            name_map = {}
            for hit in sdata.get("hits", {}).get("hits", []):
                src = hit["_source"]
                acc = src.get("accession_no", "").replace("-", "")
                names = src.get("display_names", [])
                company_cik = cik.zfill(10)
                filer_name = next(
                    (n.split("(CIK")[0].strip() for n in names
                     if company_cik not in n.replace(" ", "")), "Insider"
                )
                name_map[src.get("file_date", "")] = filer_name
        except Exception:
            name_map = {}

        trades = []
        for i, (form, date) in enumerate(zip(forms, dates)):
            if form != "4":
                continue
            if date < since:
                continue
            filer = name_map.get(date, "Insider")
            trades.append({"filer": filer, "date": date, "form": form})
            if len(trades) >= 5:
                break

        # Deduplicate by filer+date
        seen = set()
        deduped = []
        for t in trades:
            key = f"{t['filer']}_{t['date']}"
            if key not in seen:
                seen.add(key)
                deduped.append(t)
        trades = deduped

        if not trades:
            return {"available": True, "trades": [], "company": company_name,
                    "summary": f"No Form 4 filings since {since}"}

        return {
            "available": True,
            "company":   company_name,
            "trades":    trades,
            "count":     len(trades),
            "summary":   f"{len(trades)} insider filing{'s' if len(trades)>1 else ''} in last {days} days",
            "sentiment": "ACTIVE" if len(trades) >= 3 else "NEUTRAL",
        }

    except Exception as e:
        return {"available": False, "reason": str(e)}


def format_insider_for_prompt(ticker: str) -> str:
    """Format insider data for Perseus context."""
    data = get_insider_trades(ticker)
    if not data.get("available"):
        return ""
    if not data.get("trades"):
        return "No recent insider Form 4 activity."
    lines = [f"**Insider Activity (SEC Form 4) — Last 30 days: {data['summary']}**"]
    for t in data["trades"][:3]:
        lines.append(f"- {t['filer']} filed Form {t['form']} on {t['date']}")
    return "\n".join(lines)
