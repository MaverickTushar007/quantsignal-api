import groq
from core.config import settings


def _groq_reasoning(prompt: str) -> str:
    client = groq.Groq(api_key=settings.groq_api_key)
    resp = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=200,
        temperature=0.2,
    )
    return resp.choices[0].message.content.strip()


def _rule_based_reasoning(ticker, direction, probability, confluence_bulls, top_features):
    pct = f"{probability*100:.0f}%"
    feat_str = ", ".join(top_features[:3]) if top_features else "technical indicators"
    return (f"ML ensemble signals {direction} with {pct} confidence for {ticker}. "
            f"Primary drivers: {feat_str}. {confluence_bulls}/9 confluence factors align.")


def get_reasoning(ticker, name, direction, probability, confluence_bulls,
                  top_features, news_headlines, current_price=0,
                  take_profit=0, stop_loss=0, atr=0, volume_ratio=1.0, model_agreement=0):
    headlines_str = "\n".join(f"- {h}" for h in news_headlines[:3]) or "No recent news."
    feat_str = ", ".join(top_features[:4]) if top_features else "momentum and trend"
    confidence_label = "HIGH" if probability >= 0.72 else "MEDIUM" if probability >= 0.58 else "LOW"
    prompt = f"""You are a quantitative analyst writing a trade note. Be specific, direct, and market-aware.
Do NOT use generic phrases like "potential reversal due to volatility" or "not financial advice".
Write exactly 3 sentences. Each sentence must contain specific numbers from the data below.

SIGNAL DATA:
- Asset: {name} ({ticker})
- Direction: {direction} | ML Confidence: {probability*100:.1f}% ({confidence_label}) | Model Agreement: {model_agreement*100:.0f}%
- Current Price: {current_price} | Take Profit: {take_profit} | Stop Loss: {stop_loss}
- ATR: {atr} | Confluence: {confluence_bulls}/9 bullish factors
- Top ML drivers: {feat_str}
- Recent news:
{headlines_str}

FORMAT:
Sentence 1: What the ML model sees technically RIGHT NOW (cite top 2 drivers with signal direction).
Sentence 2: Risk/reward at these specific price levels (use actual TP and SL numbers).
Sentence 3: The single biggest risk that would invalidate this signal (specific to this asset).

Do not start with "The" or "This". Start with the asset name or a verb."""
    try:
        return _groq_reasoning(prompt)
    except Exception:
        return _rule_based_reasoning(ticker, direction, probability, confluence_bulls, top_features)
