"""
api/routes/weekly_report.py
Weekly performance report — generates and optionally emails a summary.
GET  /weekly-report          → latest report for user
POST /weekly-report/generate → generate + store + email report
"""
import logging
import os
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Header
from typing import Optional

router = APIRouter()
log = logging.getLogger(__name__)


def _sb():
    from supabase import create_client
    return create_client(
        os.environ.get("SUPABASE_URL", ""),
        os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_ANON_KEY", "")
    )


def _send_report_email(to_email: str, report: dict) -> bool:
    try:
        import httpx
        key = os.environ.get("RESEND_API_KEY", "")
        if not key:
            return False

        week_str   = report.get("week_ending", "this week")
        top_signals = report.get("top_signals", [])
        regime_sum  = report.get("regime_summary", {})
        perf        = report.get("performance", {})
        commentary  = report.get("commentary", "")

        sig_rows = "".join([
            f"<tr><td>{s.get('symbol')}</td><td>{s.get('direction')}</td>"
            f"<td>{s.get('ev_score', 0):.2f}%</td><td>{s.get('regime')}</td></tr>"
            for s in top_signals[:5]
        ])

        html = f"""
        <div style="font-family:monospace;background:#0a0f1e;color:#e0e0e0;padding:24px;border-radius:8px">
          <h2 style="color:#00ff88">📊 QuantSignal Weekly Report</h2>
          <p style="color:#888">Week ending {week_str}</p>

          <h3 style="color:#00aaff">🎯 Top Signals This Week</h3>
          <table style="width:100%;border-collapse:collapse">
            <tr style="color:#888;font-size:11px">
              <th align="left">Symbol</th><th>Direction</th><th>EV</th><th>Regime</th>
            </tr>
            {sig_rows or '<tr><td colspan="4" style="color:#888">No signals fired this week</td></tr>'}
          </table>

          <h3 style="color:#00aaff">🌍 Regime Distribution</h3>
          <p>Bull: {regime_sum.get('bull', 0)} | Bear: {regime_sum.get('bear', 0)} | Ranging: {regime_sum.get('ranging', 0)}</p>

          <h3 style="color:#00aaff">📈 Performance</h3>
          <p>Signals fired: {perf.get('total_signals', 0)} | 
             Win rate: {perf.get('win_rate', 'N/A')} | 
             Avg EV: {perf.get('avg_ev', 'N/A')}</p>

          <h3 style="color:#ffd700">💡 Perseus Weekly Commentary</h3>
          <p style="color:#ccc;line-height:1.6">{commentary}</p>

          <p style="color:#444;font-size:11px;margin-top:24px">
            QuantSignal · Intelligent Signals. Confident Trades.<br>
            <a href="https://quantsignal.in" style="color:#00ff88">quantsignal.in</a>
          </p>
        </div>
        """

        r = httpx.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "from":    "QuantSignal <reports@quantsignal.in>",
                "to":      [to_email],
                "subject": f"📊 Your QuantSignal Weekly Report — {week_str}",
                "html":    html,
            },
            timeout=10,
        )
        return r.status_code == 200
    except Exception as e:
        log.warning(f"[weekly_report] email failed: {e}")
        return False


def _generate_report(user_id: str) -> dict:
    now       = datetime.now(timezone.utc)
    week_ago  = now - timedelta(days=7)
    week_str  = now.strftime("%B %d, %Y")

    report = {
        "user_id":       user_id,
        "week_ending":   week_str,
        "generated_at":  now.isoformat(),
        "top_signals":   [],
        "regime_summary": {"bull": 0, "bear": 0, "ranging": 0},
        "performance":   {},
        "commentary":    "",
    }

    # Pull signal history from Supabase
    try:
        sb  = _sb()
        res = sb.table("signal_history") \
            .select("symbol,direction,ev_score,regime,probability,outcome,created_at") \
            .gte("created_at", week_ago.isoformat()) \
            .order("ev_score", desc=True) \
            .limit(50).execute()
        rows = res.data or []

        # Top 5 by EV
        report["top_signals"] = [r for r in rows if r.get("ev_score")][:5]

        # Regime distribution
        for r in rows:
            reg = r.get("regime", "")
            if reg in report["regime_summary"]:
                report["regime_summary"][reg] += 1

        # Performance stats
        total   = len(rows)
        wins    = sum(1 for r in rows if r.get("outcome") == "win")
        evs     = [r["ev_score"] for r in rows if r.get("ev_score")]
        avg_ev  = round(sum(evs) / len(evs), 2) if evs else None
        win_rate = f"{wins/total:.0%}" if total else "N/A"
        report["performance"] = {
            "total_signals": total,
            "wins":          wins,
            "win_rate":      win_rate,
            "avg_ev":        f"{avg_ev:+.2f}%" if avg_ev else "N/A",
        }
    except Exception as e:
        log.warning(f"[weekly_report] history fetch failed: {e}")

    # Perseus commentary via Groq
    try:
        import groq
        key = os.environ.get("GROQ_API_KEY", "")
        if key:
            perf    = report["performance"]
            regimes = report["regime_summary"]
            client  = groq.Groq(api_key=key)
            resp    = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{
                    "role": "system",
                    "content": "You are Perseus, a quant trading AI. Write a 3-sentence weekly market commentary."
                }, {
                    "role": "user",
                    "content": (
                        f"This week: {perf.get('total_signals', 0)} signals fired, "
                        f"{perf.get('win_rate', 'N/A')} win rate, avg EV {perf.get('avg_ev', 'N/A')}. "
                        f"Regime mix: bull={regimes['bull']}, bear={regimes['bear']}, ranging={regimes['ranging']}. "
                        f"Write a 3-sentence commentary on market conditions and what traders should watch next week."
                    )
                }],
                max_tokens=150,
                temperature=0.4,
            )
            report["commentary"] = resp.choices[0].message.content.strip()
    except Exception as e:
        log.debug(f"[weekly_report] commentary failed: {e}")
        perf = report["performance"]
        report["commentary"] = (
            f"This week saw {perf.get('total_signals', 0)} signals fire with a "
            f"{perf.get('win_rate', 'N/A')} win rate and {perf.get('avg_ev', 'N/A')} average EV. "
            f"Monitor regime shifts closely heading into next week."
        )

    return report


@router.get("/weekly-report")
def get_weekly_report(x_user_id: Optional[str] = Header(None)):
    user_id = x_user_id or "default"
    try:
        sb  = _sb()
        res = sb.table("weekly_reports") \
            .select("*").eq("user_id", user_id) \
            .order("generated_at", desc=True).limit(1).execute()
        if res.data:
            return res.data[0]
    except Exception as e:
        log.debug(f"[weekly_report] fetch failed: {e}")
    return {"message": "No report yet — POST /weekly-report/generate to create one"}


@router.post("/weekly-report/generate")
def generate_weekly_report(x_user_id: Optional[str] = Header(None)):
    user_id = x_user_id or "default"
    report  = _generate_report(user_id)

    # Store in Supabase
    try:
        _sb().table("weekly_reports").upsert({
            "user_id":      user_id,
            "generated_at": report["generated_at"],
            "report":       report,
        }).execute()
    except Exception as e:
        log.warning(f"[weekly_report] store failed: {e}")

    # Email if user has email in preferences
    try:
        from app.api.routes.preferences import _load_prefs
        prefs = _load_prefs(user_id)
        email = prefs.get("email")
        if email:
            sent = _send_report_email(email, report)
            report["email_sent"] = sent
        else:
            report["email_sent"] = False
            report["email_note"] = "Add email to preferences to receive reports"
    except Exception as e:
        log.debug(f"[weekly_report] email step failed: {e}")

    return report
