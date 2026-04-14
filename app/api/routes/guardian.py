"""
api/guardian.py
Trade Guardian — personalized risk check before entering a trade.
Plain English. No jargon. Talks in dollars, not percentages.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.core.config import settings
import groq

router = APIRouter()

class GuardianRequest(BaseModel):
    symbol: str
    direction: str
    current_price: float
    take_profit: float
    stop_loss: float
    probability: float
    kelly_size: float
    atr: float
    risk_reward: float
    position_amount: float
    total_capital: float

@router.post("/guardian/check", tags=["guardian"])
def guardian_check(req: GuardianRequest):
    # Risk as % of position size
    risk_pct    = abs(req.current_price - req.stop_loss) / req.current_price
    reward_pct  = abs(req.take_profit - req.current_price) / req.current_price

    max_loss_dollars  = round(req.position_amount * risk_pct, 2)
    max_gain_dollars  = round(req.position_amount * reward_pct, 2)
    capital_at_risk   = round((req.position_amount / req.total_capital) * 100, 1)

    # Kelly recommended position
    kelly_dollars     = round((req.kelly_size / 100) * req.total_capital, 2)

    # Volatility regime
    atr_pct = (req.atr / req.current_price) * 100
    if atr_pct > 3:
        volatility_label = "very high volatility"
        volatility_note  = "The market is moving aggressively right now."
        vol_multiplier   = 0.6
    elif atr_pct > 1.5:
        volatility_label = "elevated volatility"
        volatility_note  = "Expect larger-than-normal price swings."
        vol_multiplier   = 0.8
    else:
        volatility_label = "normal volatility"
        volatility_note  = "Market conditions are relatively stable."
        vol_multiplier   = 1.0

    recommended_dollars = round(min(kelly_dollars * vol_multiplier, req.total_capital * 0.15), 2)
    recommended_pct     = round((recommended_dollars / req.total_capital) * 100, 1)
    sizing_ratio        = req.position_amount / recommended_dollars if recommended_dollars > 0 else 1

    # Verdict
    if req.direction == "HOLD":
        verdict       = "WAIT"
        verdict_emoji = "⏸"
        verdict_color = "#ffd700"
        verdict_msg   = "No clear edge right now"
        verdict_sub   = "The model doesn't see a strong enough signal to risk your money. Waiting is a valid trade."
    elif capital_at_risk > 20 or sizing_ratio > 2.5:
        verdict       = "TOO RISKY"
        verdict_emoji = "🔴"
        verdict_color = "#ff4466"
        verdict_msg   = "Your position size is too large"
        verdict_sub   = f"Putting ${req.position_amount:,.0f} here risks {capital_at_risk}% of your capital on one trade. That's how accounts blow up."
    elif capital_at_risk > 10 or sizing_ratio > 1.5:
        verdict       = "PROCEED WITH CAUTION"
        verdict_emoji = "🟡"
        verdict_color = "#ffd700"
        verdict_msg   = "Valid trade but consider sizing down"
        verdict_sub   = f"The signal is real, but ${req.position_amount:,.0f} is on the aggressive side for your capital."
    elif req.probability < 0.52:
        verdict       = "WEAK SIGNAL"
        verdict_emoji = "🟡"
        verdict_color = "#ffd700"
        verdict_msg   = "Low confidence — smaller size makes sense"
        verdict_sub   = "The model sees a slight edge but not a strong one. Size down to match the conviction level."
    else:
        verdict       = "GOOD TO GO"
        verdict_emoji = "🟢"
        verdict_color = "#00ff88"
        verdict_msg   = "Well-sized trade with a real edge"
        verdict_sub   = "Your sizing is sensible and the signal has conviction. Risk is managed."

    worst_case = f"If this trade goes against you, you could lose up to ${max_loss_dollars:,.0f} — that's {round((max_loss_dollars/req.total_capital)*100,1)}% of your capital."
    best_case  = f"If the signal plays out, you could make up to ${max_gain_dollars:,.0f} — a {req.risk_reward}:1 return on your risk."

    if req.position_amount <= recommended_dollars * 1.1:
        sizing_advice = f"Your size of ${req.position_amount:,.0f} is in line with what Guardian recommends. Well managed."
    else:
        sizing_advice = f"Guardian recommends ${recommended_dollars:,.0f} for this trade ({recommended_pct}% of your capital). {volatility_note}"

    # AI analysis via Groq
    ai_analysis = ""
    if settings.groq_api_key:
        try:
            prompt = f"""You are Trade Guardian, a protective trading mentor. Give a 3-sentence analysis of this trade in plain English — no jargon, no bullet points, talk like a smart friend who trades.

Signal: {req.direction} on {req.symbol}
Current price: ${req.current_price:,.0f}
Model confidence: {req.probability*100:.1f}%
User wants to risk: ${req.position_amount:,.0f} of ${req.total_capital:,.0f} total capital
Max possible loss: ${max_loss_dollars:,.0f}
Max possible gain: ${max_gain_dollars:,.0f}
Market volatility: {volatility_label}
Guardian verdict: {verdict}
Recommended size: ${recommended_dollars:,.0f}

Be direct, protective, and honest. Mention the specific dollar amounts. End with one clear actionable sentence."""

            client = groq.Groq(api_key=settings.groq_api_key)
            _groq_models = ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"]
            response = client.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=180,
                temperature=0.7,
            )
            ai_analysis = response.choices[0].message.content.strip()
        except Exception:
            ai_analysis = ""

    return {
        "verdict": verdict,
        "verdict_emoji": verdict_emoji,
        "verdict_color": verdict_color,
        "verdict_msg": verdict_msg,
        "verdict_sub": verdict_sub,
        "worst_case": worst_case,
        "best_case": best_case,
        "sizing_advice": sizing_advice,
        "max_loss_dollars": max_loss_dollars,
        "max_gain_dollars": max_gain_dollars,
        "capital_at_risk": capital_at_risk,
        "recommended_dollars": recommended_dollars,
        "recommended_pct": recommended_pct,
        "volatility_label": volatility_label,
        "ai_analysis": ai_analysis,
        "risk_reward": req.risk_reward,
        "probability_pct": round(req.probability * 100, 1),
    }
