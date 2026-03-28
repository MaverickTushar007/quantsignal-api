"""
Morning Briefing Generator — runs daily, stores in Supabase.
Uses Groq to synthesize overnight signals into a plain-English briefing.
"""
import os, logging
from datetime import datetime, timezone, date
log = logging.getLogger(__name__)

def generate_morning_briefing() -> dict:
    """
    Generate and store a morning briefing.
    Returns {"status": "ok", "briefing": "..."}
    """
    try:
        today = date.today().isoformat()

        # Check if already generated today
        sb = _sb()
        existing = sb.table("morning_briefings").select("id,briefing_text") \
            .eq("date", today).limit(1).execute()
        if existing.data:
            return {"status": "cached", "briefing": existing.data[0]["briefing_text"]}

        # Gather data
        signals      = _get_overnight_signals()
        errors       = _get_recent_errors()
        cb_status    = _get_circuit_breaker()
        ev_summary   = _get_ev_summary()

        # Build prompt
        prompt = _build_briefing_prompt(signals, errors, cb_status, ev_summary, today)

        # Call Groq
        import groq
        from app.core.config import settings
        client = groq.Groq(api_key=settings.groq_api_key)
        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": "You are Perseus, a quant system briefing agent. Be concise, factual, and data-driven. No fluff."},
                {"role": "user",   "content": prompt}
            ],
            max_tokens=600,
            temperature=0.2,
        )
        briefing_text = resp.choices[0].message.content.strip()

        # Store in Supabase
        sb.table("morning_briefings").insert({
            "date":                   today,
            "briefing_text":          briefing_text,
            "signals_analyzed":       len(signals),
            "alerts_fired":           sum(1 for s in signals if s.get("alerted")),
            "errors_detected":        len(errors),
            "circuit_breaker_active": cb_status.get("active", False),
        }).execute()

        log.info(f"[morning_briefing] generated for {today}")
        return {"status": "ok", "date": today, "briefing": briefing_text}

    except Exception as e:
        log.error(f"[morning_briefing] failed: {e}")
        return {"status": "error", "error": str(e)}


def get_latest_briefing() -> dict:
    """Fetch the most recent morning briefing."""
    try:
        res = _sb().table("morning_briefings").select("*") \
            .order("date", desc=True).limit(1).execute()
        if res.data:
            return res.data[0]
        return {"briefing_text": "No briefing generated yet. Run /system/morning-briefing to generate."}
    except Exception as e:
        return {"error": str(e)}


def _get_overnight_signals() -> list:
    try:
        import psycopg2
        con = psycopg2.connect(os.environ["DATABASE_URL"])
        cur = con.cursor()
        cur.execute("""
            SELECT symbol, direction, probability, regime, outcome, confidence
            FROM signal_history
            WHERE generated_at >= NOW() - INTERVAL '24 hours'
            ORDER BY generated_at DESC
            LIMIT 30
        """)
        rows = cur.fetchall()
        con.close()
        return [{"symbol": r[0], "direction": r[1], "probability": r[2],
                 "regime": r[3], "outcome": r[4], "confidence": r[5]} for r in rows]
    except Exception as e:
        log.debug(f"[morning_briefing] signals fetch failed: {e}")
        return []


def _get_recent_errors() -> list:
    try:
        res = _sb().table("system_errors").select("component,error_type,pattern_count") \
            .eq("resolved", False) \
            .order("timestamp", desc=True).limit(10).execute()
        return res.data or []
    except Exception:
        return []


def _get_circuit_breaker() -> dict:
    try:
        from app.domain.core.circuit_breaker import check_circuit_breaker
        return check_circuit_breaker()
    except Exception:
        return {"active": False}


def _get_ev_summary() -> list:
    try:
        from app.domain.core.ev_calculator import get_all_ev_summary
        return [s for s in get_all_ev_summary() if s.get("ev") is not None]
    except Exception:
        return []


def _build_briefing_prompt(signals, errors, cb_status, ev_summary, today) -> str:
    lines = [f"Generate a morning briefing for {today}."]

    lines.append(f"\nSIGNALS (last 24h): {len(signals)} total")
    if signals:
        by_dir = {}
        for s in signals:
            d = s.get("direction", "HOLD")
            by_dir[d] = by_dir.get(d, 0) + 1
        lines.append(f"  Distribution: {by_dir}")
        evaluated = [s for s in signals if s.get("outcome")]
        if evaluated:
            wins = sum(1 for s in evaluated if s["outcome"] == "win")
            lines.append(f"  Evaluated: {len(evaluated)} | Wins: {wins} | Win rate: {wins/len(evaluated):.1%}")
        top = signals[:5]
        lines.append("  Top signals:")
        for s in top:
            lines.append(f"    {s['symbol']} {s['direction']} {s.get('probability',0):.1%} [{s.get('regime','')}]")

    lines.append(f"\nEV STATS (regime performance):")
    for ev in ev_summary[:4]:
        lines.append(f"  {ev['regime']} {ev['direction']}: EV={ev['ev']:+.2f}% WR={ev.get('win_rate',0):.1%} ({ev['total_trades']} trades)")

    lines.append(f"\nCIRCUIT BREAKER: {'🔴 ACTIVE — ' + cb_status.get('reason','') if cb_status.get('active') else '🟢 INACTIVE'}")

    if errors:
        lines.append(f"\nSYSTEM ERRORS ({len(errors)} unresolved):")
        for e in errors[:3]:
            lines.append(f"  {e['component']}/{e['error_type']} ×{e.get('pattern_count',1)}")

    lines.append("\nWrite a 4-6 sentence briefing covering: 1) Signal flow summary 2) Regime performance 3) Any risks or anomalies 4) What to watch today. Be specific with numbers.")
    return "\n".join(lines)


def _sb():
    from supabase import create_client
    return create_client(
        os.environ["SUPABASE_URL"],
        os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_ANON_KEY", "")
    )
