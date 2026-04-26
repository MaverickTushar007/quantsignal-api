"""
domain/reasoning/hybrid_retrieval.py
Phase 3.3 — Hybrid Retrieval.
Upgrades basic dense-only RAG to dense + BM25 keyword boost + rerank.
Wraps existing search_research (pgvector) — does not replace it.
"""
import re
import logging
from typing import List, Dict

log = logging.getLogger(__name__)

# Finance terms that get score boost when they appear in results
FINANCE_TERMS = {
    "earnings", "revenue", "ebitda", "eps", "guidance", "buyback",
    "dividend", "dilution", "leverage", "margin", "regime", "volatility",
    "momentum", "mean reversion", "correlation", "beta", "alpha", "sharpe",
    "drawdown", "kelly", "expected value", "confluence", "signal",
    "nse", "bse", "nifty", "sensex", "fii", "dii", "delivery", "oi",
    "pcr", "vix", "atr", "rsi", "macd", "breakout", "support", "resistance",
}


class HybridRetriever:
    """
    Combines:
    - Dense retrieval: pgvector semantic search (existing search_research)
    - Sparse boost: BM25-style keyword matching for finance terms
    - Rerank: score = 0.7 * semantic + 0.3 * keyword_boost
    """

    def retrieve(
        self,
        query: str,
        top_k: int = 5,
        query_date: str | None = None,
        symbol: str = None,
        mode: str = "auto",
    ) -> str:
        """
        Drop-in replacement for search_research.
        Returns formatted string of top chunks (same contract as rag.py).
        """
        try:
            from app.domain.reasoning.rag import search_research, _get_model, _get_client

            # 1. Dense retrieval — fetch more than needed, rerank after
            fetch_k = min(top_k * 3, 9)
            raw_chunks = self._dense_retrieve(_get_model, _get_client, query, fetch_k, mode)

            if not raw_chunks:
                # Fallback to string result from original function
                return search_research(query, top_k=top_k, mode=mode)

            # 2. Keyword boost
            boosted = self._boost(query, raw_chunks, symbol)

            # 3. Rerank and slice
            reranked = sorted(boosted, key=lambda x: x["_score"], reverse=True)[:top_k]

            # W4.3 — Point-in-time filter
            if query_date:
                try:
                    from datetime import datetime
                    cutoff = datetime.fromisoformat(query_date)
                    pit_filtered = []
                    for r in reranked:
                        doc_date = r.get("date") or r.get("published_at") or r.get("created_at")
                        if doc_date:
                            try:
                                if datetime.fromisoformat(str(doc_date)[:10]) <= cutoff:
                                    pit_filtered.append(r)
                            except Exception:
                                pit_filtered.append(r)
                        else:
                            pit_filtered.append(r)
                    reranked = pit_filtered
                except Exception as _pit:
                    log.warning(f"[hybrid_retrieval] PIT filter failed: {_pit}")

            return "\n\n".join(r["content"][:300] for r in reranked)

        except Exception as e:
            log.warning(f"[hybrid_retrieval] failed, falling back to dense: {e}")
            try:
                from app.domain.reasoning.rag import search_research
                return search_research(query, top_k=top_k, mode=mode)
            except Exception:
                return ""

    def _dense_retrieve(self, get_model, get_client, query: str, k: int, mode: str) -> List[Dict]:
        try:
            model  = get_model()
            client = get_client()
            embedding = model.encode(query).tolist()
            result = client.rpc("match_research_chunks", {
                "query_embedding": embedding,
                "match_count": k,
            }).execute()
            if not result.data:
                return []
            rows = result.data
            # Attach base semantic score (pgvector similarity, 0-1)
            for i, r in enumerate(rows):
                r["_score"] = 1.0 - (i / len(rows)) * 0.4  # rank-based proxy
            return rows
        except Exception as e:
            log.warning(f"[hybrid_retrieval] dense retrieve failed: {e}")
            return []

    def _boost(self, query: str, chunks: List[Dict], symbol: str = None) -> List[Dict]:
        query_terms = set(re.findall(r'\w+', query.lower()))
        finance_hits = query_terms & FINANCE_TERMS

        for chunk in chunks:
            content_lower = chunk.get("content", "").lower()
            keyword_score = 0.0

            # Finance term match boost
            for term in finance_hits:
                if term in content_lower:
                    keyword_score += 0.15

            # Symbol match boost
            if symbol and symbol.upper().replace(".NS", "").replace(".BO", "") in chunk.get("content", "").upper():
                keyword_score += 0.20

            # Recency boost — prefer GS papers (already marked in rag.py)
            if chunk.get("paper") in {"GS_QUANT", "LOPEZ_PRADO", "CHAN"}:
                keyword_score += 0.05

            # Combined score
            chunk["_score"] = chunk.get("_score", 0.5) * 0.70 + min(keyword_score, 0.30)

        return chunks


# Module-level singleton
hybrid_retriever = HybridRetriever()
