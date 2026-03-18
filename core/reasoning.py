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


import json
import asyncio

async def stream_chat(symbol: str, message: str, history: list):
    """
    Agent Workspace SSE generator.
    Yields JSON chunks starting with "data: " for Server-Sent Events.
    """
    def _yield_status(msg: str):
        return f"data: {json.dumps({'type': 'status', 'message': msg})}\n\n"

    try:
        # Step 1: Status Updates
        yield _yield_status(f"Initializing Perseus workspace for {symbol}...")
        await asyncio.sleep(0.5)

        # Step 2: Fetch Signal Data
        yield _yield_status(f"Fetching real-time indicators and signal data...")
        from pathlib import Path
        cache_path = Path("data/signals_cache.json")
        sig_data = None
        if cache_path.exists():
            cache = json.loads(cache_path.read_text())
            sig_data = cache.get(symbol)

        await asyncio.sleep(0.5)

        # Step 3: RAG Retrieval
        yield _yield_status("Scanning quantitative research corpus (RAG)...")
        rag_text = "No academic context available."
        try:
            from core.rag import search_research
            if sig_data:
                feat = ", ".join(sig_data.get("top_features", []))
                dir_ = sig_data.get("direction", "neutral")
                qs = f"{dir_} signal {feat} momentum volatility technicals"
                chunks = search_research(qs, top_k=2)
                rag_text = "\n".join([f"- {c}" for c in chunks])
        except Exception:
            pass

        await asyncio.sleep(0.5)

        # Build System Prompt
        sys_prompt = "You are an elite quantitative trading AI assistant. Respond strictly in clean Markdown.\n"
        if sig_data:
            sys_prompt += f"\nLIVE ASSET CONTEXT:\nAsset: {symbol}\nSignal: {sig_data.get('direction')} ({sig_data.get('probability', 0)*100:.1f}%)"
            sys_prompt += f"\nFeatures: {', '.join(sig_data.get('top_features', []))}"
            sys_prompt += f"\nConfluence: {sig_data.get('confluence_bulls', 0)}/9 Bullish"
            sys_prompt += f"\nKey Levels - Entry: {sig_data.get('current_price')}, TP: {sig_data.get('take_profit')}, SL: {sig_data.get('stop_loss')}"
        else:
            sys_prompt += f"\nLIVE ASSET CONTEXT: Data for {symbol} is currently unavailable. Discuss purely technically."

        sys_prompt += f"\n\nACADEMIC RAG CONTEXT:\n{rag_text}"
        sys_prompt += "\n\nINSTRUCTIONS: Answer the user's question precisely. If they ask about the current signal, explain the exact indicators listed above. If they ask general concepts, use the RAG context."

        # Connect to Groq Async
        if not settings.groq_api_key:
            yield _yield_status("Error: No Groq API Key found.")
            return

        from groq import AsyncGroq
        client = AsyncGroq(api_key=settings.groq_api_key)

        messages = [{"role": "system", "content": sys_prompt}]
        for m in history:
            messages.append({"role": m.get("role", "user"), "content": m.get("content", "")})
        messages.append({"role": "user", "content": message})

        yield _yield_status("Generating neural response...")

        # Stream LLM tokens
        stream = await client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=messages,
            stream=True,
            temperature=0.3,
            max_tokens=600
        )

        async for chunk in stream:
            token = chunk.choices[0].delta.content
            if token:
                yield f"data: {json.dumps({'type': 'token', 'content': token})}\n\n"

        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    except Exception as e:
        yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
