"""
documents/ingest.py
Downloads and indexes financial documents for core tickers.
Can be run manually or called by the APScheduler cron.
"""
import os
import logging
import requests
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Core documents to index — add more as needed
DOCUMENTS = [
    {
        "ticker": "RELIANCE.NS",
        "doc_type": "annual_report",
        "doc_name": "Reliance Industries Annual Report 2023-24",
        "url": "https://www.ril.com/DownloadFiles/IRDownloads/AR2023-24.pdf",
    },
    {
        "ticker": "HDFCBANK.NS",
        "doc_type": "annual_report",
        "doc_name": "HDFC Bank Annual Report 2023-24",
        "url": "https://www.hdfcbank.com/content/bbp/repositories/723fb80a-2dde-42a3-9793-7ae1be57c87f/?path=/Personal/About%20us/Investor%20Relations%20%26%20Financials/Annual%20Reports/Annual%20Report%202023-24.pdf",
    },
    {
        "ticker": "INFY.NS",
        "doc_type": "annual_report",
        "doc_name": "Infosys Annual Report 2023-24",
        "url": "https://www.infosys.com/investors/reports-filings/annual-report/annual/Documents/infosys-ar-24.pdf",
    },
    {
        "ticker": "TCS.NS",
        "doc_type": "earnings",
        "doc_name": "TCS Q3 FY25 Earnings Release",
        "url": "https://www.tcs.com/content/dam/tcs/investor-relations/financial-statements/2024-25/q3/press-release/TCS-Q3-FY25-Press-Release.pdf",
    },
    {
        "ticker": "ICICIBANK.NS",
        "doc_type": "earnings",
        "doc_name": "ICICI Bank Q3 FY25 Earnings Release",
        "url": "https://www.icicibank.com/content/dam/icicibank/india/managed-assets/docs/investor-relations/2024-2025/quarterly-results/q3fy2025/press-release-q3-2025.pdf",
    },
]


def _download_pdf(url: str, dest_path: str) -> bool:
    """Download a PDF from URL to dest_path. Returns True on success."""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; QuantSignal/1.0)"
        }
        response = requests.get(url, headers=headers, timeout=60, stream=True)
        response.raise_for_status()
        with open(dest_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
        size_kb = Path(dest_path).stat().st_size // 1024
        logger.info(f"[ingest] Downloaded {size_kb}KB → {dest_path}")
        return True
    except Exception as e:
        logger.error(f"[ingest] Download failed for {url}: {e}")
        return False


def _already_indexed(ticker: str, doc_name: str) -> bool:
    """Check if a document is already indexed in Supabase."""
    try:
        import os
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
    """
    Download and index a single document.
    Skips if already indexed unless force=True.
    Returns doc_id on success, None on failure.
    """
    ticker = doc["ticker"]
    doc_name = doc["doc_name"]
    doc_type = doc["doc_type"]
    url = doc["url"]

    if not force and _already_indexed(ticker, doc_name):
        logger.info(f"[ingest] Already indexed: {ticker} — {doc_name}")
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
        )
        return doc_id

    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


def run_full_ingestion(force: bool = False) -> dict:
    """
    Index all documents in DOCUMENTS list.
    Called by APScheduler weekly cron.
    Returns summary dict.
    """
    logger.info(f"[ingest] Starting full ingestion — {len(DOCUMENTS)} documents")
    results = {"success": [], "skipped": [], "failed": []}

    for doc in DOCUMENTS:
        key = f"{doc['ticker']}:{doc['doc_name']}"
        try:
            doc_id = ingest_document(doc, force=force)
            if doc_id:
                results["success"].append(key)
                logger.info(f"[ingest] ✅ {key} → {doc_id}")
            else:
                results["skipped"].append(key)
        except Exception as e:
            results["failed"].append(key)
            logger.error(f"[ingest] ❌ {key}: {e}")

    logger.info(f"[ingest] Done — success: {len(results['success'])}, "
                f"skipped: {len(results['skipped'])}, "
                f"failed: {len(results['failed'])}")
    return results


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    force = "--force" in sys.argv
    results = run_full_ingestion(force=force)
    print(results)
