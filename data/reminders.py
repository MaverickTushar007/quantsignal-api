"""
data/reminders.py
Supabase reminder storage + Resend email delivery.
"""
import os
import resend
from supabase import create_client
from datetime import datetime, timezone
from typing import Optional

# Load env vars — works both locally and on Railway
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

SUPABASE_URL = os.environ.get("SUPABASE_URL") or os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_ANON_KEY") or os.getenv("SUPABASE_ANON_KEY")
RESEND_KEY   = os.environ.get("RESEND_API_KEY") or os.getenv("RESEND_API_KEY")

resend.api_key = RESEND_KEY

def _get_supabase():
    """Lazy init supabase client — avoids startup crash if env not loaded yet."""
    return create_client(SUPABASE_URL, SUPABASE_KEY)

LEAD_TIME = {"HIGH": 60, "MEDIUM": 30, "LOW": 15}

def save_reminder(
    email: str,
    event_id: str,
    event_name: str,
    event_time: str,
    impact: str,
    playbook_bull: str = "",
    playbook_bear: str = "",
) -> dict:
    """Save a reminder to Supabase. Returns saved record or error."""
    try:
        # Check if already subscribed
        existing = _get_supabase().table("event_reminders").select("id").eq(
            "email", email
        ).eq("event_id", event_id).execute()

        if existing.data:
            return {"status": "already_subscribed"}

        lead = LEAD_TIME.get(impact.upper(), 30)

        record = {
            "email":             email,
            "event_id":          event_id,
            "event_name":        event_name,
            "event_time":        event_time,
            "impact":            impact.upper(),
            "playbook_bull":     playbook_bull,
            "playbook_bear":     playbook_bear,
            "lead_time_minutes": lead,
            "sent":              False,
        }

        result = _get_supabase().table("event_reminders").insert(record).execute()
        return {"status": "ok", "data": result.data}

    except Exception as e:
        return {"status": "error", "error": str(e)}


def send_reminder_email(reminder: dict) -> bool:
    """Send the reminder email via Resend."""
    try:
        impact_color = {
            "HIGH":   "#ff4466",
            "MEDIUM": "#f59e0b",
            "LOW":    "rgba(255,255,255,0.4)",
        }.get(reminder["impact"], "#ffffff")

        html = f"""
        <div style="background:#0a0a0f;padding:32px;font-family:monospace;max-width:600px;margin:0 auto;">
          <div style="border-bottom:1px solid rgba(255,255,255,0.08);padding-bottom:16px;margin-bottom:24px;">
            <span style="color:#00ff88;font-size:11px;font-weight:700;letter-spacing:0.15em;">● QUANTSIGNAL ALERT</span>
          </div>

          <h2 style="color:#ffffff;font-size:20px;margin:0 0 8px 0;">{reminder["event_name"]}</h2>
          <p style="color:rgba(255,255,255,0.4);font-size:12px;margin:0 0 24px 0;">
            Starting in {reminder["lead_time_minutes"]} minutes
          </p>

          <div style="display:inline-block;background:rgba(255,255,255,0.06);
                      border:1px solid {impact_color};border-radius:6px;
                      padding:4px 12px;margin-bottom:24px;">
            <span style="color:{impact_color};font-size:10px;font-weight:700;">
              {reminder["impact"]} IMPACT
            </span>
          </div>

          <div style="background:rgba(0,255,136,0.06);border:1px solid rgba(0,255,136,0.15);
                      border-radius:10px;padding:16px;margin-bottom:12px;">
            <div style="color:#00ff88;font-size:9px;font-weight:700;
                        letter-spacing:0.1em;margin-bottom:8px;">↑ BULLISH SCENARIO</div>
            <p style="color:rgba(255,255,255,0.7);font-size:13px;margin:0;">
              {reminder["playbook_bull"] or "Better than expected → positive market reaction likely."}
            </p>
          </div>

          <div style="background:rgba(255,68,102,0.06);border:1px solid rgba(255,68,102,0.15);
                      border-radius:10px;padding:16px;margin-bottom:32px;">
            <div style="color:#ff4466;font-size:9px;font-weight:700;
                        letter-spacing:0.1em;margin-bottom:8px;">↓ BEARISH SCENARIO</div>
            <p style="color:rgba(255,255,255,0.7);font-size:13px;margin:0;">
              {reminder["playbook_bear"] or "Worse than expected → negative market reaction likely."}
            </p>
          </div>

          <div style="border-top:1px solid rgba(255,255,255,0.06);padding-top:16px;">
            <a href="https://quantsignal-web.vercel.app/dashboard"
               style="color:#00ff88;font-size:11px;text-decoration:none;">
              → Open QuantSignal Dashboard
            </a>
          </div>

          <p style="color:rgba(255,255,255,0.15);font-size:10px;margin-top:24px;">
            QuantSignal · Not financial advice · 
            <a href="#" style="color:rgba(255,255,255,0.15);">Unsubscribe</a>
          </p>
        </div>
        """

        resend.Emails.send({
            "from":    "QuantSignal <onboarding@resend.dev>",
            "to":      [reminder["email"]],
            "subject": f"⏰ {reminder['event_name']} starts in {reminder['lead_time_minutes']} min",
            "html":    html,
        })
        return True

    except Exception as e:
        print(f"Email send failed: {e}")
        return False


def check_and_fire_reminders():
    """Called every 5 minutes — fire any due reminders."""
    try:
        from datetime import timedelta
        now = datetime.now(timezone.utc)

        # Get all unsent reminders
        result = _get_supabase().table("event_reminders").select("*").eq(
            "sent", False
        ).execute()

        fired = 0
        for reminder in result.data:
            event_time = datetime.fromisoformat(
                reminder["event_time"].replace("Z", "+00:00")
            )
            lead = reminder["lead_time_minutes"]
            fire_at = event_time - timedelta(minutes=lead)

            # Fire if we're within 5 minutes of the fire time
            if fire_at <= now <= event_time:
                success = send_reminder_email(reminder)
                if success:
                    _get_supabase().table("event_reminders").update(
                        {"sent": True}
                    ).eq("id", reminder["id"]).execute()
                    fired += 1
                    print(f"Fired reminder: {reminder['event_name']} → {reminder['email']}")

        if fired:
            print(f"Fired {fired} reminders")

    except Exception as e:
        print(f"Reminder check failed: {e}")
