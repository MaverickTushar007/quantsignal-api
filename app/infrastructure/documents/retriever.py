"""
Document retriever — finds relevant financial documents for Perseus context.
Called before Perseus generates narrative to ground reasoning in real policy.
"""
import os
import logging

log = logging.getLogger(__name__)

SUPABASE_URL = os.getenv("SUPABASE_URL") or os.getenv("NEXT_PUBLIC_SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_KEY", "")


def get_relevant_context(symbol: str, direction: str,
                          limit: int = 3) -> str:
    """
    Find relevant RBI/SEBI/NSE documents for a given signal.
    Returns formatted context string for Perseus prompt injection.
    """
    if not SUPABASE_URL or not SUPABASE_KEY:
        return ""
    try:
        from supabase import create_client
        from app.infrastructure.documents.embedder import embed_text
        sb = create_client(SUPABASE_URL, SUPABASE_KEY)

        # Build a query that captures the signal context
        query = f"{symbol} {direction} signal market outlook monetary policy"
        embedding = embed_text(query)
        vec_str = "[" + ",".join(f"{v:.6f}" for v in embedding) + "]"

        result = sb.rpc("match_documents", {
            "query_embedding": vec_str,
            "doc_type_filter": None,
            "match_count": limit,
        }).execute()

        docs = result.data or []
        if not docs:
            return ""

        lines = ["RELEVANT FINANCIAL CONTEXT:"]
        for doc in docs:
            source = doc.get("source", "")
            title  = doc.get("title", "")[:80]
            content = doc.get("content", "")[:300]
            date   = str(doc.get("published_at", ""))[:10]
            lines.append(f"[{source} — {date}] {title}: {content}...")

        return "\n".join(lines)

    except Exception as e:
        log.warning(f"[retriever] failed: {e}")
        return ""


def format_for_perseus(context: str) -> str:
    """Format document context for Perseus prompt injection."""
    if not context:
        return ""
    return f"""
## POLICY & MARKET CONTEXT
{context}

Use the above context to ground your reasoning in real financial policy 
and announcements. Reference specific sources when relevant.
"""
