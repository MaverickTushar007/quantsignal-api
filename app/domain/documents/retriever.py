"""
documents/retriever.py
Retrieves relevant page text from indexed documents using Groq.
Simple approach: score pages by keyword relevance, then summarize with LLM.
"""
import os
import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def _get_groq_client():
    from groq import Groq
    return Groq(api_key=os.environ.get("GROQ_API_KEY", ""))


def _score_pages(pages: list[dict], question: str) -> list[dict]:
    """
    Score pages by keyword overlap with question.
    Returns pages sorted by relevance score descending.
    """
    keywords = set(re.findall(r'\b\w{4,}\b', question.lower()))
    scored = []
    for p in pages:
        text_lower = p["text"].lower()
        score = sum(1 for kw in keywords if kw in text_lower)
        if score > 0:
            scored.append({**p, "_score": score})
    return sorted(scored, key=lambda x: x["_score"], reverse=True)


def _summarize_with_groq(question: str, context: str, doc_name: str) -> str:
    """
    Use Groq to extract relevant info from page text.
    """
    try:
        client = _get_groq_client()
        prompt = f"""You are analyzing a financial document: {doc_name}

Question: {question}

Relevant document excerpts:
{context[:4000]}

Extract and summarize only the information directly relevant to the question.
Be concise — 3-5 sentences max. Include specific numbers/figures if present.
If the excerpts don't contain relevant info, say so briefly."""

        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=300,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"[retriever] Groq summarize failed: {e}")
        return ""


def retrieve_for_ticker(
    ticker: str,
    question: str,
    max_docs: int = 2,
) -> Optional[str]:
    """
    Main entry point. Returns formatted context string or None.
    """
    try:
        from app.domain.documents.indexer import get_indexed_docs, get_doc_pages

        docs = get_indexed_docs(ticker)
        if not docs:
            return None

        # Prefer earnings docs, take most recent
        docs_sorted = sorted(
            docs,
            key=lambda d: (d["doc_type"] == "earnings", d["indexed_at"]),
            reverse=True
        )[:max_docs]

        all_context = []

        for doc in docs_sorted:
            try:
                pages = get_doc_pages(doc)
                if not pages:
                    continue

                # Score and pick top 3 most relevant pages
                scored = _score_pages(pages, question)
                if not scored:
                    # Fallback: use first 3 pages
                    scored = pages[:3]

                top_pages = scored[:3]
                context = "\n\n".join(
                    f"[Page {p['page']}]\n{p['text'][:800]}"
                    for p in top_pages
                )

                summary = _summarize_with_groq(question, context, doc["doc_name"])
                if summary:
                    all_context.append(
                        f"**{doc['doc_name']} ({doc['doc_type']})**\n{summary}"
                    )

            except Exception as e:
                logger.warning(f"[retriever] doc {doc.get('doc_id')} failed: {e}")
                continue

        return "\n\n---\n\n".join(all_context) if all_context else None

    except Exception as e:
        logger.error(f"[retriever] retrieve_for_ticker failed for {ticker}: {e}")
        return None
