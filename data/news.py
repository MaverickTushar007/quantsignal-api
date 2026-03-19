"""
data/news.py
FinSight News & Sentiment Engine.
RSS Aggregation + Institutional-grade LLM Sentiment Scoring.
"""

import feedparser
import re
import groq
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List

from core.config import settings

# --- Configuration ---

RSS_FEEDS = [
    "https://feeds.finance.yahoo.com/rss/2.0/headline",
    "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    "https://feeds.marketwatch.com/marketwatch/topstories/",
    "https://www.investing.com/rss/news.rss",
    "https://cryptonews.com/news/feed/",
    "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",
    "https://seekingalpha.com/market_currents.xml",
]

BULL_WORDS = {"surge", "rally", "breakout", "bullish", "gain", "high",
              "upgrade", "beat", "strong", "growth", "record", "buy"}
BEAR_WORDS = {"crash", "fall", "drop", "bearish", "loss", "low",
              "downgrade", "miss", "weak", "decline", "sell", "fear"}

ALIASES = {
    "BTC-USD":   ["bitcoin", "btc"],
    "ETH-USD":   ["ethereum", "eth", "ether"],
    "SOL-USD":   ["solana", "sol"],
    "NVDA":      ["nvidia", "nvda"],
    "AAPL":      ["apple", "aapl", "iphone"],
    "TSLA":      ["tesla", "tsla", "elon"],
    "MSFT":      ["microsoft", "msft"],
    "GOOGL":     ["google", "alphabet", "googl"],
    "AMZN":      ["amazon", "amzn", "aws"],
    "META":      ["meta", "facebook"],
    "GC=F":      ["gold", "xau"],
    "CL=F":      ["oil", "crude", "wti"],
    "EURUSD=X":  ["euro", "eur", "eurusd"],
    "USDINR=X":  ["rupee", "inr", "india"],
}

# --- Data Models ---

@dataclass
class NewsItem:
    title:     str
    summary:   str
    source:    str
    url:       str
    sentiment: str    # BULLISH | BEARISH | NEUTRAL

# --- Sentiment Logic ---

def _llm_sentiment_score(headlines: List[str]) -> float:
    """
    Institutional analysis: Uses Groq to extract sentiment from a cluster of headlines.
    Returns a score between -1.0 (Bearish) and 1.0 (Bullish).
    """
    if not settings.groq_api_key or not headlines:
        return 0.0

    client = groq.Groq(api_key=settings.groq_api_key)
    headline_text = "\n".join([f"- {h}" for h in headlines[:5]])
    
    prompt = f"""Analyze the collective financial sentiment of these headlines. 
Return ONLY a single number between -1.0 (extremely bearish) and 1.0 (extremely bullish).
0.0 is neutral. Do not provide any explanation or text—only the number.

HEADLINES:
{headline_text}
"""
    try:
        resp = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=10,
            temperature=0,
        )
        score_text = resp.choices[0].message.content.strip()
        
        # Regex extraction to catch the number regardless of LLM "yap"
        match = re.search(r"[-+]?\d*\.?\d+", score_text)
        return float(match.group()) if match else 0.0
    except Exception:
        return 0.0


def _score_sentiment(text: str) -> str:
    """Fast keyword fallback for individual news items."""
    words = set(text.lower().split())
    bulls = len(words & BULL_WORDS)
    bears = len(words & BEAR_WORDS)
    if bulls > bears: return "BULLISH"
    elif bears > bulls: return "BEARISH"
    return "NEUTRAL"


# --- RSS Logic ---

_rss_cache: List[dict] = []
_cache_loaded = False

def _load_rss_cache():
    global _rss_cache, _cache_loaded
    if _cache_loaded:
        return
    
    articles = []
    for url in RSS_FEEDS:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:30]:
                articles.append({
                    "title":   entry.get("title", ""),
                    "summary": entry.get("summary", "")[:200],
                    "source":  feed.feed.get("title", url),
                    "url":     entry.get("link", ""),
                })
        except Exception:
            continue
    _rss_cache = articles
    _cache_loaded = True


def get_news(symbol: str, limit: int = 5) -> List[NewsItem]:
    """Retrieves and filters relevant news for a specific symbol."""
    _load_rss_cache()
    
    def _matches(text: str, sym: str) -> bool:
        text_lower = text.lower()
        terms = ALIASES.get(sym, [sym.lower().replace("-usd","").replace("=x","")])
        return any(t in text_lower for t in terms)

    results = []
    for art in _rss_cache:
        combined_text = f"{art['title']} {art['summary']}"
        if _matches(combined_text, symbol):
            results.append(NewsItem(
                title=art["title"],
                summary=art["summary"],
                source=art["source"],
                url=art["url"],
                sentiment=_score_sentiment(combined_text),
            ))
        if len(results) >= limit:
            break
    return results


def get_sentiment_score(symbol: str) -> float:
    """
    Final Signal Output: Returns institutional sentiment score.
    Used for the 10% sentiment weight in the ML ensemble.
    """
    news = get_news(symbol, limit=8)
    if not news:
        return 0.0
    
    return _llm_sentiment_score([n.title for n in news])
