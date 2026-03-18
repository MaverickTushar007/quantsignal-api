"""
core/reasoning.py
LLM reasoning layer.
Chain: Groq (fast) → OpenRouter (reliable) → rule-based (always works).
Never fails — always returns a string explanation.
"""

import httpx
from core.config import settings


def _groq_reasoning(prompt: str) -> str:
    if not settings.groq_api_key:
        raise ValueError("No Groq key")
    import groq
    client = groq.Groq(api_key=settings.groq_api_key)
    resp = client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=150,
        temperature=0.3,
    )
    return resp.choices[0].message.content.strip()


def _openrouter_reasoning(prompt: str) -> str:
    if not settings.openrouter_api_key:
        raise ValueError("No OpenRouter key")
    import httpx
    resp = httpx.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={"Authorization": f"Bearer {settings.openrouter_api_key}"},
        json={
            "model": settings.openrouter_model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 150,
        },
        timeout=15,
    )
    return resp.json()["choices"][0]["message"]["content"].strip()


def _rule_based_reasoning(
    ticker: str, direction: str, probability: float,
    confluence_bulls: int, top_features: list
) -> str:
    pct = f"{probability*100:.0f}%"
    feat_str = ", ".join(top_features[:3]) if top_features else "technical indicators"
    bull_str = f"{confluence_bulls}/9 bullish indicators"
    return (
        f"ML ensemble signals {direction} with {pct} confidence for {ticker}. "
        f"Key drivers: {feat_str}. Technical confluence: {bull_str}. "
        f"This is an educational signal — not financial advice. "
        f"Always manage risk with the provided stop loss levels."
    )


def get_reasoning(
    ticker: str,
    name: str,
    direction: str,
    probability: float,
    confluence_bulls: int,
    top_features: list,
    news_headlines: list,
) -> str:
    """
    Generate plain-English reasoning for a signal.
    Tries Groq first, falls back to OpenRouter, then rule-based.
    Always returns a string — never raises.
    """
    headlines = "\n".join(f"- {h}" for h in news_headlines[:3])
    feat_str  = ", ".join(top_features[:3]) if top_features else "momentum and trend"

    try:
        from core.rag import search_research
        query = f"{direction} signal {feat_str} momentum volatility technicals"
        context_chunks = search_research(query, top_k=2)
        academic_context = "\n".join([f"- {c}" for c in context_chunks])
    except Exception:
        academic_context = "No academic context available."

    prompt = f"""You are an expert quantitative analyst. Explain this trading signal in 2-3 sentences.
Ground your explanation in the provided academic research context if relevant.

Asset: {name} ({ticker})
Signal: {direction} — {probability*100:.0f}% ML confidence
Top predictive features: {feat_str}
Technical confluence: {confluence_bulls}/9 bullish indicators
Recent news:
{headlines if headlines else "No relevant news found."}

ACADEMIC RESEARCH CONTEXT:
{academic_context}

Write a concise professional explanation of WHY this signal was generated, referencing the phenomena in the academic context (if applicable) instead of generic technical jargon (e.g., mention time series momentum, reversion, or factor confluence). 
Keep it under 75 words. End with one key risk factor based on ATR or market conditions.
Do NOT give financial advice."""

    # Try Groq first
    try:
        return _groq_reasoning(prompt)
    except Exception:
        pass

    # Try OpenRouter
    try:
        return _openrouter_reasoning(prompt)
    except Exception:
        pass

    # Always-available fallback
    return _rule_based_reasoning(ticker, direction, probability,
                                  confluence_bulls, top_features)
