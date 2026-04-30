"""
data/news.py — yfinance-based news engine (RSS feeds replaced, all dead).
"""
import re
import groq
from dataclasses import dataclass
from typing import List
from app.core.config import settings

BULL_WORDS = {"surge","rally","breakout","bullish","gain","upgrade","beat","strong","growth","record","buy","soar","jump","spike","rise","rises","risen","rising","climbs","rebounds","rebound","recover","recovery","outperform","upside","uptrend","inflow","accumulate","buying","positive","profit","profits","earnings","exceeds","adoption","approve","approved","launch","partnership","momentum","optimistic","confidence"}
BEAR_WORDS = {"crash","fall","drop","bearish","loss","low","downgrade","miss","weak","decline","sell","fear","plunge","plunges","plummets","slump","tumble","collapse","selloff","sell-off","dump","liquidation","outflow","short","shorting","resistance","rejected","warning","warns","risk","concern","worried","panic","hack","exploit","ban","crackdown","regulation","lawsuit","fraud","investigation","halted","suspended","negative","losses","missed","disappoints","weakness","downside","downtrend","correction","volatile","uncertainty","recession","inflation","hawkish","pauses","delays"}

@dataclass
class NewsItem:
    title: str
    summary: str
    source: str
    url: str
    sentiment: str

def _score_sentiment(text: str) -> str:
    words = set(text.lower().split())
    bulls = len(words & BULL_WORDS)
    bears = len(words & BEAR_WORDS)
    if bulls > bears: return "BULLISH"
    elif bears > bulls: return "BEARISH"
    return "NEUTRAL"

def _llm_sentiment_score(headlines: List[str]) -> float:
    if not settings.groq_api_key or not headlines: return 0.0
    client = groq.Groq(api_key=settings.groq_api_key)
    prompt = f"Analyze financial sentiment of these headlines. Return ONLY a number between -1.0 (bearish) and 1.0 (bullish). No text.\n\nHEADLINES:\n" + "\n".join(f"- {h}" for h in headlines[:5])
    try:
        resp = client.chat.completions.create(model="llama-3.1-8b-instant", messages=[{"role":"user","content":prompt}], max_tokens=10, temperature=0)
        match = re.search(r"[-+]?\d*\.?\d+", resp.choices[0].message.content.strip())
        return float(match.group()) if match else 0.0
    except Exception: return 0.0

def get_news(symbol: str, limit: int = 5) -> List[NewsItem]:
    try:
        import yfinance as yf
        raw_news = yf.Ticker(symbol).news or []
    except Exception: return []
    results = []
    for item in raw_news[:limit]:
        content = item.get("content", {})
        title   = content.get("title") or item.get("title", "")
        summary = content.get("summary") or item.get("summary", "")
        source  = (content.get("provider") or {}).get("displayName") or item.get("publisher", "")
        url     = (content.get("canonicalUrl") or {}).get("url") or item.get("link", "")
        if not title: continue
        results.append(NewsItem(title=title[:200], summary=summary[:300], source=source, url=url, sentiment=_score_sentiment(f"{title} {summary}")))
    return results

def get_sentiment_score(symbol: str) -> float:
    try:
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
        news = get_news(symbol, limit=10)
        if not news: return 0.0
        headlines = [n.title for n in news]
        analyzer = SentimentIntensityAnalyzer()
        vader_avg = sum(analyzer.polarity_scores(h)["compound"] for h in headlines) / len(headlines)
        groq_score = _llm_sentiment_score(headlines[:5])
        return round(max(-1.0, min(1.0, 0.4 * vader_avg + 0.6 * groq_score)), 3)
    except Exception: return 0.0


def refresh_news_cache():
    """Refresh news/sentiment cache for all tickers. Called every hour by scheduler."""
    import json
    from pathlib import Path
    from app.core.config import BASE_DIR
    from app.domain.data.universe import TICKERS

    cache = {}
    # Only refresh the 28 most-watched symbols to keep it fast
    priority_syms = [t["symbol"] for t in TICKERS if t.get("type") in ("CRYPTO", "INDEX", "STOCK")][:30]
    for sym in priority_syms:
        try:
            score = get_sentiment_score(sym)
            cache[sym] = {"sentiment": score, "ts": __import__("datetime").datetime.utcnow().isoformat()}
        except Exception:
            pass

    if cache:
        cache_path = BASE_DIR / "data/sentiment_cache.json"
        cache_path.parent.mkdir(exist_ok=True)
        cache_path.write_text(json.dumps(cache, default=str))
