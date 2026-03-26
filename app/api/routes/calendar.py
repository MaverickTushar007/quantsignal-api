"""
api/calendar.py
Live economic calendar scraped from ForexFactory.
Includes this week + next week events with AI playbooks.
"""
from fastapi import APIRouter, HTTPException
import requests
from bs4 import BeautifulSoup
import json
import time
from pathlib import Path
from datetime import datetime, timezone, timedelta

router = APIRouter()
CACHE_PATH = Path("data/calendar_cache.json")
CACHE_TTL = 604800  # 7 days — refreshed by local commit

COUNTRY_FLAGS = {
    "USD": "🇺🇸", "EUR": "🇪🇺", "GBP": "🇬🇧", "JPY": "🇯🇵",
    "CAD": "🇨🇦", "AUD": "🇦🇺", "NZD": "🇳🇿", "CHF": "🇨🇭",
}

PLAYBOOKS = {
    "Non-Farm": {"bullish": "Strong NFP → risk-on rally. BTC/ETH pump. Dollar strengthens.", "bearish": "Weak NFP → recession fears. Risk-off. BTC drops 3-5%. Gold rallies.", "assets": ["BTC", "ETH", "SPY", "GLD"]},
    "CPI": {"bullish": "CPI below forecast → disinflation. Rate cut hopes boost risk assets. BTC bullish.", "bearish": "CPI above forecast → inflation sticky. Fed stays hawkish. Risk-off, BTC dumps.", "assets": ["BTC", "ETH", "SPY", "QQQ"]},
    "FOMC": {"bullish": "Dovish tone or rate cut → liquidity injection. Risk-on. BTC and equities rally.", "bearish": "Hawkish surprise or rate hike → tightening. Risk-off selloff across crypto and equities.", "assets": ["BTC", "ETH", "SPY", "QQQ", "GLD"]},
    "Federal Funds": {"bullish": "Rate cut → liquidity positive. BTC and risk assets rally hard.", "bearish": "Rate hold or hike → tightening continues. Risk-off selloff.", "assets": ["BTC", "ETH", "SPY", "QQQ", "GLD"]},
    "GDP": {"bullish": "GDP above forecast → strong economy. Risk-on. Equities and crypto benefit.", "bearish": "GDP miss → recession risk. Safe havens outperform. BTC drops.", "assets": ["BTC", "SPY", "GLD"]},
    "Unemployment": {"bullish": "Lower unemployment → strong labor market. Mixed — could mean Fed stays tight.", "bearish": "Higher unemployment → economic weakness. Risk-off initially.", "assets": ["BTC", "ETH", "SPY"]},
    "PMI": {"bullish": "PMI above 50 → expansion. Risk-on sentiment. Equities and crypto benefit.", "bearish": "PMI below 50 → contraction. Risk-off. Dollar strengthens, BTC weakens.", "assets": ["BTC", "SPY", "EUR/USD"]},
    "Retail Sales": {"bullish": "Strong retail sales → consumer spending healthy. Risk-on, equities benefit.", "bearish": "Weak retail sales → consumer slowdown. Recession fears, risk-off.", "assets": ["SPY", "QQQ", "BTC"]},
    "Interest Rate": {"bullish": "Rate cut or dovish hold → liquidity positive. BTC and risk assets rally.", "bearish": "Rate hike or hawkish hold → tightening cycle. Risk-off selloff.", "assets": ["BTC", "ETH", "SPY", "QQQ", "GLD"]},
    "Cash Rate": {"bullish": "Rate cut → AUD weakens, risk-on. Crypto benefits.", "bearish": "Rate hike → AUD strengthens, risk-off. Crypto pressure.", "assets": ["AUD/USD", "BTC", "SPY"]},
    "Trump": {"bullish": "Pro-crypto or pro-market comments → risk-on rally.", "bearish": "Tariff threats or uncertainty → risk-off selloff.", "assets": ["BTC", "SPY", "QQQ"]},
    "Powell": {"bullish": "Dovish comments → rate cut expectations rise. BTC and equities rally.", "bearish": "Hawkish tone → higher for longer. Risk-off, BTC dumps.", "assets": ["BTC", "ETH", "SPY", "GLD"]},
    "PCE": {"bullish": "PCE below forecast → Fed's preferred inflation measure cooling. Rate cut hopes.", "bearish": "PCE above forecast → inflation sticky. Fed stays hawkish.", "assets": ["BTC", "SPY", "QQQ"]},
    "Durable Goods": {"bullish": "Strong orders → manufacturing expanding. Risk-on.", "bearish": "Weak orders → economic slowdown. Risk-off.", "assets": ["SPY", "BTC"]},
}

def get_playbook(title: str) -> dict:
    for key, pb in PLAYBOOKS.items():
        if key.lower() in title.lower():
            return pb
    return {"bullish": "Better than expected → positive market reaction likely.", "bearish": "Worse than expected → negative market reaction likely.", "assets": ["SPY", "BTC"]}

def parse_ff_date(date_str: str, time_str: str, year: int = 2026) -> str:
    """Convert ForexFactory date string to ISO format."""
    try:
        if not date_str:
            return ""
        # Remove day name: "Mon Mar 16" -> "Mar 16"
        parts = date_str.strip().split()
        if len(parts) == 3:
            month_day = f"{parts[1]} {parts[2]} {year}"
        elif len(parts) == 2:
            month_day = f"{parts[0]} {parts[1]} {year}"
        else:
            return ""
        
        if time_str and time_str.strip() and time_str.strip() not in ['', 'Tentative', 'All Day']:
            dt_str = f"{month_day} {time_str.strip()}"
            dt = datetime.strptime(dt_str, "%b %d %Y %I:%M%p")
        else:
            dt = datetime.strptime(month_day, "%b %d %Y")
        
        # ForexFactory times are EST (UTC-4 during DST, UTC-5 standard)
        est_offset = timedelta(hours=-4)
        dt_aware = dt.replace(tzinfo=timezone(est_offset))
        return dt_aware.isoformat()
    except Exception:
        return ""

def scrape_forexfactory() -> list:
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
    events = []
    current_date = ""

    for url in [
        'https://www.forexfactory.com/calendar',
        'https://www.forexfactory.com/calendar?week=next',
    ]:
        try:
            resp = requests.get(url, headers=headers, timeout=10)
            soup = BeautifulSoup(resp.text, 'html.parser')
            rows = soup.select('tr.calendar__row')

            for row in rows:
                date_el = row.select_one('.calendar__date')
                if date_el and date_el.text.strip():
                    current_date = date_el.text.strip()

                currency = row.select_one('.calendar__currency')
                event = row.select_one('.calendar__event')
                time_el = row.select_one('.calendar__time')
                impact_el = row.select_one('.calendar__impact span')
                forecast_el = row.select_one('.calendar__forecast')
                previous_el = row.select_one('.calendar__previous')

                if not event or not currency or not impact_el:
                    continue

                impact_class = ' '.join(impact_el.get('class', []))
                if 'impact-red' in impact_class or 'impact-ora' in impact_class:
                    impact = 'High'
                elif 'impact-yel' in impact_class:
                    impact = 'Medium'
                else:
                    continue

                currency_text = currency.text.strip()
                if currency_text not in COUNTRY_FLAGS:
                    continue

                event_title = event.text.strip()
                time_text = time_el.text.strip() if time_el else ''
                iso_date = parse_ff_date(current_date, time_text)
                pb = get_playbook(event_title)

                events.append({
                    "title": event_title,
                    "country": currency_text,
                    "flag": COUNTRY_FLAGS.get(currency_text, "🌍"),
                    "date": iso_date,
                    "time_display": time_text,
                    "date_display": current_date,
                    "impact": impact,
                    "forecast": forecast_el.text.strip() if forecast_el else '',
                    "previous": previous_el.text.strip() if previous_el else '',
                    "bullish_scenario": pb["bullish"],
                    "bearish_scenario": pb["bearish"],
                    "affected_assets": pb["assets"],
                })
        except Exception as e:
            print(f"FF scrape failed for {url}: {e}")

    return events

def fetch_calendar() -> list:
    if CACHE_PATH.exists():
        try:
            cache = json.loads(CACHE_PATH.read_text())
            if time.time() - cache.get("timestamp", 0) < CACHE_TTL:
                return cache.get("data", [])
        except Exception:
            pass

    events = scrape_forexfactory()
    CACHE_PATH.write_text(json.dumps({"timestamp": time.time(), "data": events}))
    return events

@router.get("/calendar/debug", tags=["calendar"])
def debug_calendar():
    try:
        import requests
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        resp = requests.get("https://www.forexfactory.com/calendar", headers=headers, timeout=10)
        return {"status": resp.status_code, "length": len(resp.text), "preview": resp.text[:200]}
    except Exception as e:
        return {"error": str(e)}

@router.get("/calendar/events", tags=["calendar"])
def get_calendar_events():
    now = datetime.now(timezone.utc)
    events = fetch_calendar()

    upcoming = []
    past = []

    for e in events:
        try:
            if not e.get("date"):
                upcoming.append(e)
                continue
            event_date = datetime.fromisoformat(e["date"])
            if event_date.tzinfo is None:
                event_date = event_date.replace(tzinfo=timezone.utc)
            if event_date >= now:
                upcoming.append(e)
            else:
                past.append(e)
        except Exception:
            upcoming.append(e)

    past.sort(key=lambda x: x.get("date", ""), reverse=True)
    past = past[:15]

    return {"upcoming": upcoming, "past": past, "count": len(upcoming) + len(past)}

from pydantic import BaseModel

class ReminderRequest(BaseModel):
    email: str
    event_id: str
    event_name: str
    event_time: str
    impact: str
    playbook_bull: str = ""
    playbook_bear: str = ""

@router.post("/calendar/remind", tags=["calendar"])
def set_reminder(req: ReminderRequest):
    from app.domain.data.reminders import save_reminder
    result = save_reminder(
        email=req.email,
        event_id=req.event_id,
        event_name=req.event_name,
        event_time=req.event_time,
        impact=req.impact,
        playbook_bull=req.playbook_bull,
        playbook_bear=req.playbook_bear,
    )
    if result["status"] == "error":
        raise HTTPException(status_code=500, detail=result["error"])
    return result

@router.get("/calendar/remind-debug", tags=["calendar"])
def debug_remind():
    import os
    results = {}
    results["SUPABASE_URL"] = os.environ.get("SUPABASE_URL", "NOT SET")[:30]
    results["SUPABASE_KEY"] = os.environ.get("SUPABASE_ANON_KEY", "NOT SET")[:20]
    results["RESEND_KEY"] = os.environ.get("RESEND_API_KEY", "NOT SET")[:10]
    try:
        from app.domain.data.reminders import _get_supabase
        sb = _get_supabase()
        test = sb.table("event_reminders").select("id").limit(1).execute()
        results["supabase_status"] = "ok"
        results["row_count"] = len(test.data)
    except Exception as e:
        results["supabase_error"] = str(e)
    return results
