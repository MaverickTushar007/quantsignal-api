"""
documents/retriever.py
Retrieves relevant page text from indexed documents.
Primary: Groq. Fallback: OpenRouter when Groq TPD is exhausted.
"""
import os
import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def _groq_summarize(prompt: str) -> Optional[str]:
    try:
        from groq import Groq
        client = Groq(api_key=os.environ.get("GROQ_API_KEY", ""))
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=300,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        err = str(e)
        if "429" in err or "rate_limit" in err.lower() or "TPD" in err or "token" in err.lower():
            logger.warning("[retriever] Groq rate limited — will try fallback")
            return None
        # Auth errors / other failures — don't fallback, just fail
        logger.error(f"[retriever] Groq failed (no fallback): {e}")
        raise


def _openrouter_summarize(prompt: str) -> Optional[str]:
    try:
        import requests
        key = os.environ.get("OPENROUTER_API_KEY", "")
        if not key:
            return None
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://quantsignal.in",
            },
            json={
                "model": "meta-llama/llama-3.3-70b-instruct",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
                "max_tokens": 300,
            },
            timeout=30,
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.error(f"[retriever] OpenRouter fallback failed: {e}")
        return None


def _summarize(prompt: str) -> Optional[str]:
    """Try Groq first, fall back to OpenRouter."""
    result = _groq_summarize(prompt)
    if result is not None:
        return result
    logger.info("[retriever] Using OpenRouter fallback")
    return _openrouter_summarize(prompt)


def _score_pages(pages: list[dict], question: str) -> list[dict]:
    keywords = set(re.findall(r'\b\w{4,}\b', question.lower()))
    scored = []
    for p in pages:
        text_lower = p["text"].lower()
        score = sum(1 for kw in keywords if kw in text_lower)
        if score > 0:
            scored.append({**p, "_score": score})
    return sorted(scored, key=lambda x: x["_score"], reverse=True)


def retrieve_for_ticker(
    ticker: str,
    question: str,
    max_docs: int = 2,
) -> Optional[str]:
    try:
        from app.domain.documents.indexer import get_indexed_docs, get_doc_pages

        docs = get_indexed_docs(ticker)
        if not docs:
            return None

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

                scored = _score_pages(pages, question)
                top_pages = (scored or pages)[:3]

                context = "\n\n".join(
                    f"[Page {p['page']}]\n{p['text'][:800]}"
                    for p in top_pages
                )

                prompt = f"""You are analyzing a financial document: {doc['doc_name']}

Question: {question}

Relevant document excerpts:
{context[:4000]}

Extract and summarize only the information directly relevant to the question.
Be concise — 3-5 sentences max. Include specific numbers/figures if present.
If the excerpts don't contain relevant info, say so briefly."""

                summary = _summarize(prompt)
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
