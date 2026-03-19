"""
api/calendar.py
Live economic calendar from ForexFactory with AI-generated playbooks.
"""
from fastapi import APIRouter
from fastapi.responses import JSONResponse
import requests
import json
import time
from pathlib import Path

router = APIRouter()
CACHE_PATH = Path("data/calendar_cache.json")
CACHE_TTL = 3600  # 1 hour

IMPACT_PRIORITY = {"High": 3, "Medium": 2, "Low": 1}

COUNTRY_CURRENCIES = {
    "USD": "🇺🇸", "EUR": "🇪🇺", "GBP": "🇬🇧", "JPY": "🇯🇵",
    "CAD": "🇨🇦", "AUD": "🇦🇺", "NZD": "🇳🇿", "CHF": "🇨🇭",
    "CNY": "🇨🇳",
}

PLAYBOOKS = {
    "Non-Farm": {
        "bullish": "Strong NFP + low unemployment → risk-on rally. BTC/ETH likely pump. Dollar strengthens.",
        "bearish": "Weak NFP → recession fears. Risk-off. BTC may drop 3-5%. Gold rallies.",
        "assets": ["BTC", "ETH", "SPY", "GLD"]
    },
    "CPI": {
        "bullish": "CPI below forecast → disinflation narrative. Rate cut hopes boost risk assets. BTC bullish.",
        "bearish": "CPI above forecast → inflation sticky. Fed stays hawkish. Risk-off, BTC dumps.",
        "assets": ["BTC", "ETH", "SPY", "QQQ"]
    },
    "FOMC": {
        "bullish": "Dovish tone or rate cut → liquidity injection. Risk-on. BTC and equities rally hard.",
        "bearish": "Hawkish surprise or rate hike → tightening. Risk-off selloff across crypto and equities.",
        "assets": ["BTC", "ETH", "SPY", "QQQ", "GLD"]
    },
    "GDP": {
        "bullish": "GDP above forecast → strong economy. Risk-on. Equities and crypto benefit.",
        "bearish": "GDP miss → recession risk. Safe havens (Gold, JPY) outperform. BTC drops.",
        "assets": ["BTC", "SPY", "GLD"]
    },
    "Unemployment": {
        "bullish": "Lower unemployment → strong labor market. Mixed for crypto — could mean Fed stays tight.",
        "bearish": "Higher unemployment → economic weakness. Risk-off initially, but may accelerate rate cuts.",
        "assets": ["BTC", "ETH", "SPY"]
    },
    "PMI": {
        "bullish": "PMI above 50 → expansion. Risk-on sentiment. Equities and crypto benefit.",
        "bearish": "PMI below 50 → contraction. Risk-off. Dollar strengthens, BTC weakens.",
        "assets": ["BTC", "SPY", "EUR/USD"]
    },
    "Retail Sales": {
        "bullish": "Strong retail sales → consumer spending healthy. Risk-on, equities benefit.",
        "bearish": "Weak retail sales → consumer slowdown. Recession fears, risk-off.",
        "assets": ["SPY", "QQQ", "BTC"]
    },
    "Interest Rate": {
        "bullish": "Rate cut or hold with dovish guidance → liquidity positive. BTC and risk assets rally.",
        "bearish": "Rate hike or hawkish hold → tightening cycle continues. Risk-off selloff.",
        "assets": ["BTC", "ETH", "SPY", "QQQ", "GLD"]
    },
}

def get_playbook(title: str) -> dict:
    for key, pb in PLAYBOOKS.items():
        if key.lower() in title.lower():
            return pb
    return {
        "bullish": "Better than expected → positive market reaction likely.",
        "bearish": "Worse than expected → negative market reaction likely.",
        "assets": ["SPY", "BTC"]
    }

def fetch_calendar() -> list:
    cached = None
    if CACHE_PATH.exists():
        try:
            cache = json.loads(CACHE_PATH.read_text())
            if time.time() - cache.get("timestamp", 0) < CACHE_TTL:
                return cache.get("data", [])
        except Exception:
            pass

    try:
        # Get this week + next week
        urls = [
            "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
            "https://nfs.faireconomy.media/ff_calendar_nextweek.json",
        ]
        all_events = []
        for url in urls:
            try:
                resp = requests.get(url, timeout=8)
                all_events.extend(resp.json())
            except Exception:
                pass

        # Filter high + medium impact only
        filtered = [
            e for e in all_events
            if e.get("impact") in ["High", "Medium"]
            and e.get("country") in ["USD", "EUR", "GBP", "JPY", "CAD", "AUD", "CNY"]
        ]

        # Sort by date
        filtered.sort(key=lambda x: x.get("date", ""))

        # Enrich with playbooks
        enriched = []
        for e in filtered[:20]:  # top 20 events
            pb = get_playbook(e.get("title", ""))
            flag = COUNTRY_CURRENCIES.get(e.get("country", ""), "🌍")
            enriched.append({
                "title": e.get("title", ""),
                "country": e.get("country", ""),
                "flag": flag,
                "date": e.get("date", ""),
                "impact": e.get("impact", ""),
                "forecast": e.get("forecast", ""),
                "previous": e.get("previous", ""),
                "bullish_scenario": pb["bullish"],
                "bearish_scenario": pb["bearish"],
                "affected_assets": pb["assets"],
            })

        CACHE_PATH.write_text(json.dumps({
            "timestamp": time.time(),
            "data": enriched
        }))
        return enriched

    except Exception as e:
        print(f"Calendar fetch failed: {e}")
        return []

@router.get("/calendar/events", tags=["calendar"])
def get_calendar_events():
    events = fetch_calendar()
    return {"events": events, "count": len(events)}
