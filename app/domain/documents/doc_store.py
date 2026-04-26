"""
domain/documents/doc_store.py
W3.4 — Persist analyzed documents to Supabase.
Table: analyzed_documents
"""
import logging
import os
from typing import Optional

log = logging.getLogger(__name__)


def _client():
    from supabase import create_client
    url = os.getenv("SUPABASE_URL", "")
    key = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_ANON_KEY", "")
    return create_client(url, key)


def save_document(result_dict: dict, filename: str, user_id: Optional[str] = None) -> Optional[str]:
    """
    Save analyzed document to Supabase. Returns doc_id or None on failure.
    """
    try:
        sb = _client()
        row = {
            "filename":      filename,
            "doc_type":      result_dict.get("doc_type"),
            "summary":       result_dict.get("summary"),
            "key_metrics":   result_dict.get("key_metrics", {}),
            "entities":      result_dict.get("entities", []),
            "finance_schema":result_dict.get("finance_schema", {}),
            "confidence":    result_dict.get("confidence"),
            "page_count":    result_dict.get("page_count", 0),
            "table_count":   result_dict.get("table_count", 0),
            "user_id":       user_id,
        }
        res = sb.table("analyzed_documents").insert(row).execute()
        doc_id = res.data[0]["id"] if res.data else None
        log.info(f"[doc_store] saved document {filename} → {doc_id}")
        return doc_id
    except Exception as e:
        log.warning(f"[doc_store] save failed: {e}")
        return None


def get_document(doc_id: str) -> Optional[dict]:
    """Retrieve a saved document by id."""
    try:
        sb = _client()
        res = sb.table("analyzed_documents").select("*").eq("id", doc_id).single().execute()
        return res.data
    except Exception as e:
        log.warning(f"[doc_store] get failed for {doc_id}: {e}")
        return None


def list_documents(user_id: str, limit: int = 20) -> list:
    """List recent documents for a user."""
    try:
        sb = _client()
        res = (
            sb.table("analyzed_documents")
            .select("id,filename,doc_type,summary,confidence,created_at")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return res.data or []
    except Exception as e:
        log.warning(f"[doc_store] list failed: {e}")
        return []
