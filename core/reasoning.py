"""
core/reasoning.py
FinSight AI Reasoning Layer.
Orchestrates multi-model reasoning and streaming chat for the FinSight Assistant.
"""

import json
import asyncio
import httpx
from pathlib import Path
from groq import AsyncGroq
import groq

from core.config import settings

# --- Internal Helpers ---

def _groq_reasoning(prompt: str) -> str:
    if not settings.groq_api_key:
        raise ValueError("No Groq key")
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
        f"This is an educational signal — not financial advice."
    )

# --- Public API ---

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
    Generate plain-English reasoning for a signal (Static View).
    """
    headlines = "\n".join(f"- {h}" for h in news_headlines[:3])
    feat_str = ", ".join(top_features[:3]) if top_features else "momentum and trend"

    try:
        from core.rag import search_research
        query = f"{direction} signal {feat_str} momentum volatility technicals"
        academic_context = search_research(query, top_k=2)
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

Write a concise professional explanation of WHY this signal was generated.
Keep it under 75 words. End with one key risk factor.
Do NOT give financial advice."""

    try:
        return _groq_reasoning(prompt)
    except Exception:
        try:
            return _openrouter_reasoning(prompt)
        except Exception:
            return _rule_based_reasoning(ticker, direction, probability, confluence_bulls, top_features)


async def stream_chat(symbol: str, message: str, history: list):
    """
    FinSight Assistant SSE generator (Streaming Chat).
    """
    def _yield_status(msg: str):
        return f"data: {json.dumps({'type': 'status', 'message': msg})}\n\n"

    try:
        # 1. Initialize
        if symbol == "GENERIC":
            yield _yield_status("Initializing FinSight Global Intelligence...")
        else:
            yield _yield_status(f"Initializing FinSight workspace for {symbol}...")
        await asyncio.sleep(0.4)

        # 2. Context Extraction
        sig_data = None
        if symbol != "GENERIC":
            yield _yield_status(f"Syncing real-time indicators for {symbol}...")
            cache_path = Path("data/signals_cache.json")
            if cache_path.exists():
                cache = json.loads(cache_path.read_text())
                sig_data = cache.get(symbol)
        else:
            yield _yield_status("Scanning global markets and macro sentiment...")
        
        await asyncio.sleep(0.4)

        # 3. RAG Retrieval
        yield _yield_status("Accessing quantitative research corpus (RAG)...")
        rag_text = "No academic context available."
        try:
            from core.rag import search_research
            if sig_data:
                feat = ", ".join(sig_data.get("top_features", []))
                dir_ = sig_data.get("direction", "neutral")
                qs = f"{dir_} signal {feat} momentum volatility technicals"
                chunks = search_research(qs, top_k=2)
                rag_text = "\n".join([f"- {c}" for c in chunks])
            elif symbol == "GENERIC":
                chunks = search_research(message, top_k=2)
                rag_text = "\n".join([f"- {c}" for c in chunks])
        except Exception:
            pass

        yield _yield_status("Generating neural response...")

        # 4. Build Professional System Prompt
        sys_prompt = (
            "You are FinSight Elite, a high-frequency quantitative intelligence agent.\n"
            "Your purpose is to provide deep market insights and data-driven reasoning.\n"
            "STRICT RULES:\n"
            "1. NEVER give basic definitions (e.g., don't explain what BTC is).\n"
            "2. FOCUS on alpha factors, technical confluence, and risk management.\n"
            "3. USE the RAG context below to ground your reasoning.\n"
            "4. RESPOND in professional Markdown. No fluff.\n"
        )
        if sig_data:
            sys_prompt += f"\nLIVE ASSET CONTEXT for {symbol}:\n"
            sys_prompt += f"- Bias: {sig_data.get('direction')} ({sig_data.get('probability', 0)*100:.1f}% confidence)\n"
            sys_prompt += f"- Confluence: {sig_data.get('confluence_score', 'N/A')}\n"
            sys_prompt += f"- Predictive Features: {', '.join(sig_data.get('top_features', []))}\n"
            sys_prompt += f"- Key Levels: Entry @ {sig_data.get('current_price')}, TP @ {sig_data.get('take_profit')}, SL @ {sig_data.get('stop_loss')}\n"
        elif symbol == "GENERIC":
            sys_prompt += "\nMODE: Global Macro Chat. Analyze across all assets (Stocks/Forex/Crypto).\n"

        sys_prompt += f"\nACADEMIC RAG CONTEXT:\n{rag_text}\n"

        # 5. Connect to Groq Async
        if not settings.groq_api_key:
            yield _yield_status("Error: No Groq API Key found.")
            return

        client = AsyncGroq(api_key=settings.groq_api_key)
        messages = [{"role": "system", "content": sys_prompt}]
        for m in history:
            messages.append({"role": m.get("role", "user"), "content": m.get("content", "")})
        messages.append({"role": "user", "content": message})

        stream = await client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=messages,
            stream=True,
            temperature=0.2,
            max_tokens=600
        )

        async for chunk in stream:
            token = chunk.choices[0].delta.content
            if token:
                yield f"data: {json.dumps({'type': 'token', 'content': token})}\n\n"

        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    except Exception as e:
        yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
