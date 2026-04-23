"""
documents/context_builder.py
Formats retrieved document text into a Perseus system prompt block.
Same pattern as _build_agent_context() in reasoning/service.py.
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def build_document_context(ticker: str, question: str) -> str:
    """
    Returns a ## DOCUMENT INTELLIGENCE block for injection into
    Perseus system prompt, or empty string if nothing available.
    Never raises.
    """
    try:
        from app.domain.documents.retriever import retrieve_for_ticker

        if not ticker or ticker == "GENERIC":
            return ""

        context = retrieve_for_ticker(ticker, question)
        if not context:
            return ""

        block = "## DOCUMENT INTELLIGENCE\n"
        block += f"_Relevant excerpts from indexed filings for {ticker}:_\n\n"
        block += context[:2000]  # Hard cap — Perseus prompt is already tight
        block += "\n"
        return block

    except Exception as e:
        logger.warning(f"[context_builder] failed for {ticker}: {e}")
        return ""
