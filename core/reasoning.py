"""
core/reasoning.py
QuantSignal AI Reasoning Layer — market-aware, specific, actionable.
"""

import groq
import httpx
from core.config import settings


def _groq_reasoning(prompt: str) -> str:
    if not settings.groq_api_key:
        raise ValueError("No Groq key")
    client = groq.Groq(api_key=settings.groq_api_key)
    resp = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=200,
        temperature=0.2,
    )
    return resp.choices[0].message.content.strip()


def _rule_based_reasoning(
    ticker: str, direction: str, probability: float,
    confluence_bulls: int, top_features: list
) -> str:
    pct = f"{probability*100:.0f}%"
    feat_str = ", ".join(top_features[:3]) if top_features else "technical indicators"
    return (
        f"ML ensemble signals {direction} with {pct} confidence for {ticker}. "
        f"Primary drivers: {feat_str}. {confluence_bulls}/9 confluence factors align. "
        f"Risk: signal invalidated on breach of stop level."
    )


def get_reasoning(
    ticker: str,
    name: str,
    direction: str,
    probability: float,
    confluence_bulls: int,
    top_features: list,
    news_headlines: list,
    current_price: float = 0,
    take_profit: float = 0,
    stop_loss: float = 0,
    atr: float = 0,
    volume_ratio: float = 1.0,
    model_agreement: float = 0,
) -> str:
    headlines_str = "\n".join(f"- {h}" for h in news_headlines[:3]) or "No recent news."
    feat_str = ", ".join(top_features[:4]) if top_features else "momentum and trend"
    confluence_str = f"{confluence_bulls}/9 bullish" if direction == "BUY" else f"{9-confluence_bulls}/9 bearish"
    confidence_label = "HIGH" if probability >= 0.72 else "MEDIUM" if probability >= 0.58 else "LOW"
    vol_str = f"{volume_ratio:.1f}x average volume" if volume_ratio else "normal volume"
    agreement_str = f"{model_agreement*100:.0f}%" if model_agreement else "N/A"

    prompt = f"""You are a quantitative analyst writing a trade note. Be specific, direct, and market-aware.
Do NOT use generic phrases like "potential reversal due to volatility" or "not financial advice".
Write exactly 3 sentences. Each sentence must contain specific numbers from the data below.

SIGNAL DATA:
- Asset: {name} ({ticker})
- Direction: {direction} | ML Confidence: {probability*100:.1f}% ({confidence_label}) | Model Agreement: {agreement_str}
- Current Price: {current_price} | Take Profit: {take_profit} | Stop Loss: {stop_loss}
- ATR: {atr} | Volume: {vol_str}
- Confluence: {confluence_str} factors
- Top ML drivers: {feat_str}
- Recent news:
{headlines_str}

FORMAT — write exactly this structure:
Sentence 1: What the ML model sees technically RIGHT NOW (cite the top 2 drivers with their signal direction).
Sentence 2: What the risk/reward looks like at these specific price levels (use the actual TP and SL numbers).
Sentence 3: The single biggest risk that would invalidate this signal (be specific to this asset and current market).

Do not start with "The" or "This". Start with the asset name or a verb."""

    try:
        return _groq_reasoning(prompt)
    except Exception:
        return _rule_based_reasoning(ticker, direction, probability, confluence_bulls, top_features)
