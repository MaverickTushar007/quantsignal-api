"""
documents/indexer.py
Simple PDF text extractor — no PageIndex, no LLM calls during indexing.
Stores page text as JSON in Supabase workspace_path column.
"""
import os
import json
import uuid
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def _get_supabase():
    from supabase import create_client
    url = os.environ.get("SUPABASE_URL") or os.environ.get("NEXT_PUBLIC_SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_ANON_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if not url:
        raise ValueError("supabase_url is required — check SUPABASE_URL in .env")
    return create_client(url, key)


def _extract_pdf_text(pdf_path: str) -> list[dict]:
    """Extract text page by page. Returns [{"page": int, "text": str}]"""
    import PyPDF2
    pages = []
    with open(pdf_path, "rb") as f:
        reader = PyPDF2.PdfReader(f)
        for i, page in enumerate(reader.pages, 1):
            text = (page.extract_text() or "").strip()
            if text:
                pages.append({"page": i, "text": text})
    return pages


def index_document(
    ticker: str,
    doc_type: str,
    pdf_path: str,
    doc_url: Optional[str] = None,
    doc_name: Optional[str] = None,
) -> Optional[str]:
    """
    Extract PDF text and store in Supabase.
    Returns doc_id on success, None on failure.
    """
    try:
        logger.info(f"[indexer] Extracting {ticker} {doc_type}: {pdf_path}")
        pages = _extract_pdf_text(pdf_path)
        if not pages:
            logger.error(f"[indexer] No text extracted from {pdf_path}")
            return None

        doc_id = str(uuid.uuid4())
        name = doc_name or Path(pdf_path).name

        sb = _get_supabase()
        sb.table("document_index").upsert({
            "ticker": ticker,
            "doc_id": doc_id,
            "doc_name": name,
            "doc_type": doc_type,
            "doc_url": doc_url,
            "page_count": len(pages),
            "workspace_path": json.dumps(pages),
        }, on_conflict="doc_id").execute()

        logger.info(f"[indexer] ✅ {ticker} {doc_type} — {len(pages)} pages, id: {doc_id}")
        return doc_id

    except Exception as e:
        logger.error(f"[indexer] ❌ Failed to index {ticker} {doc_type}: {e}")
        return None


def get_indexed_docs(ticker: str) -> list:
    try:
        sb = _get_supabase()
        res = sb.table("document_index") \
            .select("doc_id,doc_name,doc_type,indexed_at,page_count,workspace_path") \
            .eq("ticker", ticker) \
            .order("indexed_at", desc=True) \
            .execute()
        return res.data or []
    except Exception as e:
        logger.error(f"[indexer] get_indexed_docs failed: {e}")
        return []


def get_all_indexed_tickers() -> list:
    try:
        sb = _get_supabase()
        res = sb.table("document_index").select("ticker").execute()
        return list(set(r["ticker"] for r in (res.data or [])))
    except Exception as e:
        logger.error(f"[indexer] get_all_indexed_tickers failed: {e}")
        return []


def get_doc_pages(doc: dict) -> list[dict]:
    try:
        raw = doc.get("workspace_path", "[]")
        return json.loads(raw) if raw else []
    except Exception:
        return []
