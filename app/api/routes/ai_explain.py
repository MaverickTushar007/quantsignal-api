"""
api/ai_explain.py
AI replay explanation using Groq — fast, free, server-side.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.core.config import settings
import groq

router = APIRouter()

class ExplainRequest(BaseModel):
    symbol: str
    replay_date: str
    direction: str
    confidence: str
    probability: float
    current_price: float
    actual_price_5d: float
    actual_return_5d: float
    was_correct: bool
    confluence_score: str
    confluence: list

@router.post("/replay/explain", tags=["replay"])
def explain_replay(req: ExplainRequest):
    if not settings.groq_api_key:
        raise HTTPException(status_code=500, detail="GROQ_API_KEY not set")

    confluence_str = ", ".join([f"{c['name']}: {c['signal']}" for c in req.confluence])

    prompt = f"""You are a sharp trading analyst. Explain this historical signal in 4-5 conversational sentences like talking to a trader friend. Be direct, specific, insightful. No fluff, no bullet points, just clean prose.

Asset: {req.symbol} | Date: {req.replay_date}
Price then: ${req.current_price:,.0f} | Signal: {req.direction} ({req.confidence}, {req.probability*100:.1f}%)
5 days later: ${req.actual_price_5d:,.0f} ({'+' if req.actual_return_5d > 0 else ''}{req.actual_return_5d}%) — signal was {'CORRECT' if req.was_correct else 'WRONG'}
Confluence: {req.confluence_score} | Indicators: {confluence_str}

Explain why the model made this call, what the market was doing, and the key lesson."""

    try:
        client = groq.Groq(api_key=settings.groq_api_key)
        _explain_models = ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"]
        response = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=250,
            temperature=0.7,
        )
        text = response.choices[0].message.content
        return {"explanation": text}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
