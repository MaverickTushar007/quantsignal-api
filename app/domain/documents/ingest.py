"""
documents/ingest.py
Downloads and indexes financial documents for core Indian tickers.
Uses BSE filing URLs which are stable and publicly accessible.
"""
import os
import logging
import requests
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# BSE scrip codes for core tickers
# URLs from BSE XML feed — more stable than company IR pages
DOCUMENTS = [
    {
        "ticker": "RELIANCE.NS",
        "doc_type": "earnings",
        "doc_name": "Reliance Q3 FY25 Results",
        "url": "https://www.bseindia.com/xml-data/corpfiling/AttachLive/0a6e7c9e-2a3a-4b5d-8c1f-9d3e2f4a6b8c.pdf",
        "bse_code": "500325",
    },
    {
        "ticker": "HDFCBANK.NS",
        "doc_type": "earnings",
        "doc_name": "HDFC Bank Q3 FY25 Results",
        "url": "https://www.bseindia.com/xml-data/corpfiling/AttachLive/500180_Q3FY25.pdf",
        "bse_code": "500180",
    },
    {
        "ticker": "INFY.NS",
        "doc_type": "earnings",
        "doc_name": "Infosys Q3 FY25 Results",
        "url": "https://www.bseindia.com/xml-data/corpfiling/AttachLive/500209_Q3FY25.pdf",
        "bse_code": "500209",
    },
    {
        "ticker": "TCS.NS",
        "doc_type": "earnings",
        "doc_name": "TCS Q3 FY25 Results",
        "url": "https://www.bseindia.com/xml-data/corpfiling/AttachLive/532540_Q3FY25.pdf",
        "bse_code": "532540",
    },
    {
        "ticker": "ICICIBANK.NS",
        "doc_type": "earnings",
        "doc_name": "ICICI Bank Q3 FY25 Results",
        "url": "https://www.bseindia.com/xml-data/corpfiling/AttachLive/532174_Q3FY25.pdf",
        "bse_code": "532174",
    },
    {
        "ticker": "BAJFINANCE.NS",
        "doc_type": "earnings",
        "doc_name": "Bajaj Finance Q3 FY25 Results",
        "url": "",
        "bse_code": "500034",
    },
    {
        "ticker": "WIPRO.NS",
        "doc_type": "earnings",
        "doc_name": "Wipro Q3 FY25 Results",
        "url": "",
        "bse_code": "507685",
    },
    {
        "ticker": "HCLTECH.NS",
        "doc_type": "earnings",
        "doc_name": "HCL Tech Q3 FY25 Results",
        "url": "",
        "bse_code": "532281",
    },
    {
        "ticker": "KOTAKBANK.NS",
        "doc_type": "earnings",
        "doc_name": "Kotak Bank Q3 FY25 Results",
        "url": "",
        "bse_code": "500247",
    },
    {
        "ticker": "AXISBANK.NS",
        "doc_type": "earnings",
        "doc_name": "Axis Bank Q3 FY25 Results",
        "url": "",
        "bse_code": "532215",
    },
    {
        "ticker": "MARUTI.NS",
        "doc_type": "earnings",
        "doc_name": "Maruti Suzuki Q3 FY25 Results",
        "url": "",
        "bse_code": "532500",
    },
    {
        "ticker": "SUNPHARMA.NS",
        "doc_type": "earnings",
        "doc_name": "Sun Pharma Q3 FY25 Results",
        "url": "",
        "bse_code": "524715",
    },
    {
        "ticker": "TITAN.NS",
        "doc_type": "earnings",
        "doc_name": "Titan Q3 FY25 Results",
        "url": "",
        "bse_code": "500114",
    },
]


def _fetch_latest_bse_pdf(bse_code: str) -> Optional[str]:
    """
    Fetch the latest quarterly results PDF URL from BSE for a given scrip code.
    Returns direct PDF URL or None.
    """
    try:
        api_url = (
            f"https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w"
            f"?strCat=Result&strPrevDate=&strScrip={bse_code}"
            f"&strSearch=P&strToDate=&strType=C&subcategory=Financial%20Results"
        )
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://www.bseindia.com/",
        }
        resp = requests.get(api_url, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        items = data.get("Table", []) or data.get("table", [])
        if not items:
            return None

        # Take most recent filing
        latest = items[0]
        attach = latest.get("ATTACHMENTNAME", "")
        if attach:
            return f"https://www.bseindia.com/xml-data/corpfiling/AttachLive/{attach}"
        return None

    except Exception as e:
        logger.warning(f"[ingest] BSE API fetch failed for {bse_code}: {e}")
        return None


def _download_pdf(url: str, dest_path: str) -> bool:
    try:
        headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.bseindia.com/"}
        response = requests.get(url, headers=headers, timeout=60, stream=True)
        response.raise_for_status()
        with open(dest_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
        size_kb = Path(dest_path).stat().st_size // 1024
        logger.info(f"[ingest] Downloaded {size_kb}KB → {dest_path}")
        return size_kb > 10  # reject tiny/empty files
    except Exception as e:
        logger.error(f"[ingest] Download failed for {url}: {e}")
        return False


def _already_indexed(ticker: str, doc_name: str) -> bool:
    try:
        from supabase import create_client
        sb = create_client(
            os.environ.get("SUPABASE_URL", ""),
            os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_ANON_KEY", "")
        )
        res = sb.table("document_index") \
            .select("doc_id") \
            .eq("ticker", ticker) \
            .eq("doc_name", doc_name) \
            .execute()
        return len(res.data or []) > 0
    except Exception:
        return False


def ingest_document(doc: dict, force: bool = False) -> Optional[str]:
    ticker = doc["ticker"]
    doc_name = doc["doc_name"]
    doc_type = doc["doc_type"]
    bse_code = doc.get("bse_code")

    if not force and _already_indexed(ticker, doc_name):
        logger.info(f"[ingest] Already indexed: {ticker} — {doc_name}")
        return None

    # Try BSE API first for fresh URL, fall back to hardcoded
    url = None
    if bse_code:
        url = _fetch_latest_bse_pdf(bse_code)
        if url:
            logger.info(f"[ingest] Got fresh BSE URL for {ticker}: {url}")

    if not url:
        url = doc.get("url")

    if not url:
        logger.error(f"[ingest] No URL available for {ticker}")
        return None

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        logger.info(f"[ingest] Downloading {ticker} — {doc_name}")
        if not _download_pdf(url, tmp_path):
            return None

        from app.domain.documents.indexer import index_document
        doc_id = index_document(
            ticker=ticker,
            doc_type=doc_type,
            pdf_path=tmp_path,
            doc_url=url,
            doc_name=doc_name,
        )
        return doc_id

    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


def run_full_ingestion(force: bool = False) -> dict:
    logger.info(f"[ingest] Starting full ingestion — {len(DOCUMENTS)} documents")
    results = {"success": [], "skipped": [], "failed": []}

    for doc in DOCUMENTS:
        key = f"{doc['ticker']}:{doc['doc_name']}"
        try:
            doc_id = ingest_document(doc, force=force)
            if doc_id:
                results["success"].append(key)
            else:
                results["skipped"].append(key)
        except Exception as e:
            results["failed"].append(key)
            logger.error(f"[ingest] ❌ {key}: {e}")

    logger.info(f"[ingest] Done — {results}")
    return results


# Global/Crypto documents — using public research reports
GLOBAL_DOCUMENTS = [
    {
        "ticker": "BTC-USD",
        "doc_type": "research",
        "doc_name": "Bitcoin Q4 2024 Market Report",
        "url": "https://assets.coingecko.com/reports/2024/CoinGecko-2024-Annual-Crypto-Industry-Report.pdf",
        "bse_code": None,
    },

]

# Additional Indian tickers indexed via bulk script
EXTENDED_DOCUMENTS = [
    {"ticker": "TECHM.NS",      "doc_type": "earnings", "doc_name": "Tech Mahindra Q3 FY25 Results",    "url": "", "bse_code": "532755"},
    {"ticker": "SBIN.NS",       "doc_type": "earnings", "doc_name": "SBI Q3 FY25 Results",               "url": "", "bse_code": "500112"},
    {"ticker": "ADANIENT.NS",   "doc_type": "earnings", "doc_name": "Adani Enterprises Q3 FY25 Results", "url": "", "bse_code": "512599"},
    {"ticker": "ADANIPORTS.NS", "doc_type": "earnings", "doc_name": "Adani Ports Q3 FY25 Results",       "url": "", "bse_code": "532921"},
    {"ticker": "BAJAJ-AUTO.NS", "doc_type": "earnings", "doc_name": "Bajaj Auto Q3 FY25 Results",        "url": "", "bse_code": "532977"},
    {"ticker": "DRREDDY.NS",    "doc_type": "earnings", "doc_name": "Dr Reddys Q3 FY25 Results",         "url": "", "bse_code": "500124"},
    {"ticker": "CIPLA.NS",      "doc_type": "earnings", "doc_name": "Cipla Q3 FY25 Results",             "url": "", "bse_code": "500087"},
    {"ticker": "ONGC.NS",       "doc_type": "earnings", "doc_name": "ONGC Q3 FY25 Results",              "url": "", "bse_code": "500312"},
    {"ticker": "COALINDIA.NS",  "doc_type": "earnings", "doc_name": "Coal India Q3 FY25 Results",        "url": "", "bse_code": "533278"},
    {"ticker": "TATASTEEL.NS",  "doc_type": "earnings", "doc_name": "Tata Steel Q3 FY25 Results",        "url": "", "bse_code": "500470"},
    {"ticker": "JSWSTEEL.NS",   "doc_type": "earnings", "doc_name": "JSW Steel Q3 FY25 Results",         "url": "", "bse_code": "500228"},
    {"ticker": "HINDUNILVR.NS", "doc_type": "earnings", "doc_name": "HUL Q3 FY25 Results",               "url": "", "bse_code": "500696"},
    {"ticker": "ITC.NS",        "doc_type": "earnings", "doc_name": "ITC Q3 FY25 Results",               "url": "", "bse_code": "500875"},
    {"ticker": "ASIANPAINT.NS", "doc_type": "earnings", "doc_name": "Asian Paints Q3 FY25 Results",      "url": "", "bse_code": "500820"},
    {"ticker": "LT.NS",         "doc_type": "earnings", "doc_name": "L&T Q3 FY25 Results",               "url": "", "bse_code": "500510"},
    {"ticker": "POWERGRID.NS",  "doc_type": "earnings", "doc_name": "Power Grid Q3 FY25 Results",        "url": "", "bse_code": "532898"},
    {"ticker": "NTPC.NS",       "doc_type": "earnings", "doc_name": "NTPC Q3 FY25 Results",              "url": "", "bse_code": "532555"},
    {"ticker": "GAIL.NS",       "doc_type": "earnings", "doc_name": "GAIL Q3 FY25 Results",              "url": "", "bse_code": "532155"},
    {"ticker": "IOC.NS",        "doc_type": "earnings", "doc_name": "IOC Q3 FY25 Results",               "url": "", "bse_code": "530965"},
    {"ticker": "BPCL.NS",       "doc_type": "earnings", "doc_name": "BPCL Q3 FY25 Results",              "url": "", "bse_code": "500547"},
    {"ticker": "HINDPETRO.NS",  "doc_type": "earnings", "doc_name": "HPCL Q3 FY25 Results",              "url": "", "bse_code": "500104"},
    {"ticker": "SAIL.NS",       "doc_type": "earnings", "doc_name": "SAIL Q3 FY25 Results",              "url": "", "bse_code": "500113"},
    {"ticker": "TATAPOWER.NS",  "doc_type": "earnings", "doc_name": "Tata Power Q3 FY25 Results",        "url": "", "bse_code": "500400"},
    {"ticker": "BAJAJFINSV.NS", "doc_type": "earnings", "doc_name": "Bajaj Finserv Q3 FY25 Results",     "url": "", "bse_code": "532978"},
    {"ticker": "MUTHOOTFIN.NS", "doc_type": "earnings", "doc_name": "Muthoot Finance Q3 FY25 Results",   "url": "", "bse_code": "533398"},
    {"ticker": "SBILIFE.NS",    "doc_type": "earnings", "doc_name": "SBI Life Q3 FY25 Results",          "url": "", "bse_code": "540719"},
    {"ticker": "HDFCLIFE.NS",   "doc_type": "earnings", "doc_name": "HDFC Life Q3 FY25 Results",         "url": "", "bse_code": "540777"},
    {"ticker": "INDUSINDBK.NS", "doc_type": "earnings", "doc_name": "IndusInd Bank Q3 FY25 Results",     "url": "", "bse_code": "532187"},
    {"ticker": "BANKBARODA.NS", "doc_type": "earnings", "doc_name": "Bank of Baroda Q3 FY25 Results",    "url": "", "bse_code": "532134"},
    {"ticker": "PNB.NS",        "doc_type": "earnings", "doc_name": "PNB Q3 FY25 Results",               "url": "", "bse_code": "532461"},
    {"ticker": "CANBK.NS",      "doc_type": "earnings", "doc_name": "Canara Bank Q3 FY25 Results",       "url": "", "bse_code": "532483"},
    {"ticker": "DIVISLAB.NS",   "doc_type": "earnings", "doc_name": "Divis Labs Q3 FY25 Results",        "url": "", "bse_code": "532488"},
    {"ticker": "APOLLOHOSP.NS", "doc_type": "earnings", "doc_name": "Apollo Hospitals Q3 FY25 Results",  "url": "", "bse_code": "508869"},
    {"ticker": "AUROPHARMA.NS", "doc_type": "earnings", "doc_name": "Aurobindo Pharma Q3 FY25 Results",  "url": "", "bse_code": "524804"},
    {"ticker": "LUPIN.NS",      "doc_type": "earnings", "doc_name": "Lupin Q3 FY25 Results",             "url": "", "bse_code": "500257"},
    {"ticker": "BIOCON.NS",     "doc_type": "earnings", "doc_name": "Biocon Q3 FY25 Results",            "url": "", "bse_code": "532523"},
    {"ticker": "NESTLEIND.NS",  "doc_type": "earnings", "doc_name": "Nestle India Q3 FY25 Results",      "url": "", "bse_code": "500790"},
    {"ticker": "TRENT.NS",      "doc_type": "earnings", "doc_name": "Trent Q3 FY25 Results",             "url": "", "bse_code": "500251"},
    {"ticker": "IRCTC.NS",      "doc_type": "earnings", "doc_name": "IRCTC Q3 FY25 Results",             "url": "", "bse_code": "542830"},
    {"ticker": "POLICYBZR.NS",  "doc_type": "earnings", "doc_name": "PB Fintech Q3 FY25 Results",        "url": "", "bse_code": "543390"},
    {"ticker": "ULTRACEMCO.NS", "doc_type": "earnings", "doc_name": "UltraTech Cement Q3 FY25 Results",  "url": "", "bse_code": "532538"},
    {"ticker": "SHREECEM.NS",   "doc_type": "earnings", "doc_name": "Shree Cement Q3 FY25 Results",      "url": "", "bse_code": "500387"},
    {"ticker": "HAVELLS.NS",    "doc_type": "earnings", "doc_name": "Havells Q3 FY25 Results",           "url": "", "bse_code": "517354"},
    {"ticker": "TVSMOTOR.NS",   "doc_type": "earnings", "doc_name": "TVS Motor Q3 FY25 Results",         "url": "", "bse_code": "532343"},
    {"ticker": "ASHOKLEY.NS",   "doc_type": "earnings", "doc_name": "Ashok Leyland Q3 FY25 Results",     "url": "", "bse_code": "500477"},
    {"ticker": "MRF.NS",        "doc_type": "earnings", "doc_name": "MRF Q3 FY25 Results",               "url": "", "bse_code": "500290"},
    {"ticker": "HINDALCO.NS",   "doc_type": "earnings", "doc_name": "Hindalco Q3 FY25 Results",          "url": "", "bse_code": "500440"},
    {"ticker": "VEDL.NS",       "doc_type": "earnings", "doc_name": "Vedanta Q3 FY25 Results",           "url": "", "bse_code": "500295"},
    {"ticker": "LTIM.NS",       "doc_type": "earnings", "doc_name": "LTIMindtree Q3 FY25 Results",       "url": "", "bse_code": "540005"},
    {"ticker": "PERSISTENT.NS", "doc_type": "earnings", "doc_name": "Persistent Systems Q3 FY25 Results","url": "", "bse_code": "533179"},
    {"ticker": "TATAELXSI.NS",  "doc_type": "earnings", "doc_name": "Tata Elxsi Q3 FY25 Results",       "url": "", "bse_code": "500408"},
    {"ticker": "OFSS.NS",       "doc_type": "earnings", "doc_name": "Oracle Fin Services Q3 FY25 Results","url": "","bse_code": "532466"},
    {"ticker": "ABCAPITAL.NS",  "doc_type": "earnings", "doc_name": "Aditya Birla Capital Q3 FY25 Results","url": "","bse_code": "540691"},
    {"ticker": "ABB.NS",        "doc_type": "earnings", "doc_name": "ABB India Q3 FY25 Results",         "url": "", "bse_code": "500002"},
    {"ticker": "CUMMINSIND.NS", "doc_type": "earnings", "doc_name": "Cummins India Q3 FY25 Results",     "url": "", "bse_code": "500480"},
]

# Combined list
ALL_DOCUMENTS = DOCUMENTS + GLOBAL_DOCUMENTS + EXTENDED_DOCUMENTS


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    force = "--force" in sys.argv
    results = run_full_ingestion(force=force)
    print(results)
