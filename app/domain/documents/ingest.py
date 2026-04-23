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


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    force = "--force" in sys.argv
    results = run_full_ingestion(force=force)
    print(results)
