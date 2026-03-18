"""
data/news.py
RSS news fetcher + keyword sentiment scoring.
No mock data — if feeds fail, returns empty list gracefully.
"""

import feedparser
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List

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

# Alias map for matching news to tickers
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


@dataclass
class NewsItem:
    title:     str
    summary:   str
    source:    str
    url:       str
    sentiment: str    # BULLISH | BEARISH | NEUTRAL


def _score_sentiment(text: str) -> str:
    words = set(text.lower().split())
    bulls = len(words & BULL_WORDS)
    bears = len(words & BEAR_WORDS)
    if bulls > bears:
        return "BULLISH"
    elif bears > bulls:
        return "BEARISH"
    return "NEUTRAL"


def _matches(text: str, symbol: str) -> bool:
    text_lower = text.lower()
    terms = ALIASES.get(symbol, [symbol.lower().replace("-usd","").replace("=x","")])
    return any(t in text_lower for t in terms)


# Module-level cache — fetch once per process run
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
    """
    Return relevant news items for a symbol.
    Loads RSS feeds once and filters per ticker.
    Returns empty list (not mock data) if no news found.
    """
    _load_rss_cache()
    results = []
    for art in _rss_cache:
        text = art["title"] + " " + art["summary"]
        if _matches(text, symbol):
            results.append(NewsItem(
                title=art["title"],
                summary=art["summary"],
                source=art["source"],
                url=art["url"],
                sentiment=_score_sentiment(text),
            ))
        if len(results) >= limit:
            break
    return results


def get_sentiment_score(symbol: str) -> float:
    """
    Returns sentiment score between -1 (bearish) and +1 (bullish).
    Used as the 10% sentiment blend in the ML ensemble.
    """
    news = get_news(symbol, limit=10)
    if not news:
        return 0.0
    scores = []
    for n in news:
        if n.sentiment == "BULLISH":
            scores.append(1.0)
        elif n.sentiment == "BEARISH":
            scores.append(-1.0)
        else:
            scores.append(0.0)
    return round(sum(scores) / len(scores), 3)
