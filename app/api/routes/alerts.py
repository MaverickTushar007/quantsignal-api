"""
api/alerts.py
Signal alert subscriptions — users subscribe to assets,
get emailed when BUY/SELL signals fire during daily cache refresh.
"""
import os, resend
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from supabase import create_client

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

router = APIRouter()

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_ANON_KEY")
RESEND_KEY   = os.environ.get("RESEND_API_KEY")
resend.api_key = RESEND_KEY

def _sb():
    return create_client(SUPABASE_URL, SUPABASE_KEY)

class AlertSubscribe(BaseModel):
    email: str
    symbols: List[str]  # e.g. ["BTC-USD", "RELIANCE.NS"]
    directions: Optional[List[str]] = ["BUY", "SELL"]  # which signals to alert on

class AlertUnsubscribe(BaseModel):
    email: str
    symbol: Optional[str] = None  # None = unsubscribe all

@router.post("/alerts/subscribe", tags=["alerts"])
def subscribe_alerts(body: AlertSubscribe):
    """Subscribe email to signal alerts for given symbols."""
    try:
        sb = _sb()
        added = []
        for symbol in body.symbols:
            # Check if already subscribed
            existing = sb.table("signal_alerts").select("id").eq(
                "email", body.email
            ).eq("symbol", symbol).execute()
            
            if not existing.data:
                sb.table("signal_alerts").insert({
                    "email": body.email,
                    "symbol": symbol,
                    "directions": body.directions,
                    "active": True,
                }).execute()
                added.append(symbol)

        # Send confirmation email
        if added:
            _send_confirmation(body.email, added)

        return {
            "status": "ok",
            "subscribed": added,
            "message": f"You'll be alerted when signals fire for {', '.join(added)}"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/alerts/unsubscribe", tags=["alerts"])
def unsubscribe_alerts(body: AlertUnsubscribe):
    """Unsubscribe from signal alerts."""
    try:
        sb = _sb()
        q = sb.table("signal_alerts").update({"active": False}).eq("email", body.email)
        if body.symbol:
            q = q.eq("symbol", body.symbol)
        q.execute()
        return {"status": "ok", "message": "Unsubscribed successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/alerts/subscriptions/{email}", tags=["alerts"])
def get_subscriptions(email: str):
    """Get all active alert subscriptions for an email."""
    try:
        result = _sb().table("signal_alerts").select("symbol,directions,created_at").eq(
            "email", email
        ).eq("active", True).execute()
        return {"email": email, "subscriptions": result.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

def fire_signal_alerts(new_cache: dict, old_cache: dict):
    """
    Called after cache rebuild — compare old vs new signals,
    fire Telegram + emails for any direction changes to subscribed users.
    Deduped per symbol with 6h cooldown persisted in Supabase.
    """
    from app.domain.alerts.dedup import should_alert
    from app.domain.alerts.telegram import send_telegram, format_signal_alert
    try:
        sb = _sb()
        # Find signals that changed direction AND pass dedup
        changed = []
        for symbol, new_sig in new_cache.items():
            old_sig = old_cache.get(symbol, {})
            direction_changed = old_sig.get("direction") != new_sig.get("direction")
            is_actionable = new_sig["direction"] in ["BUY", "SELL"]
            if direction_changed and is_actionable and should_alert(symbol):
                changed.append({
                        "symbol": symbol,
                        "display": new_sig.get("display", symbol),
                        "name": new_sig.get("name", symbol),
                        "direction": new_sig["direction"],
                        "probability": new_sig.get("probability", 0),
                        "confidence": new_sig.get("confidence", ""),
                        "current_price": new_sig.get("current_price", 0),
                        "type": new_sig.get("type", ""),
                    })

        if not changed:
            print("No signal direction changes — no alerts to fire")
            return 0

        print(f"Signal changes detected: {[c['symbol'] for c in changed]}")

        # Get all active subscriptions for changed symbols
        symbols = [c["symbol"] for c in changed]
        result = sb.table("signal_alerts").select("email,symbol,directions").eq(
            "active", True
        ).in_("symbol", symbols).execute()

        # Group alerts by email
        email_map = {}
        for sub in result.data:
            sig_change = next((c for c in changed if c["symbol"] == sub["symbol"]), None)
            if not sig_change:
                continue
            if sig_change["direction"] not in (sub.get("directions") or ["BUY", "SELL"]):
                continue
            email = sub["email"]
            if email not in email_map:
                email_map[email] = []
            email_map[email].append(sig_change)

        # Fire Telegram alerts for each changed signal
        for sig_change in changed:
            try:
                full_sig = new_cache.get(sig_change["symbol"], {})
                full_sig["symbol"] = sig_change["symbol"]
                msg = format_signal_alert(full_sig)
                send_telegram(msg)
                print(f"Telegram alert sent: {sig_change['symbol']} → {sig_change['direction']}")
            except Exception as te:
                print(f"Telegram alert failed for {sig_change['symbol']}: {te}")

        # Fire emails
        fired = 0
        for email, signals in email_map.items():
            success = _send_signal_alert(email, signals)
            if success:
                fired += 1
                print(f"Alert fired → {email}: {[s['symbol'] for s in signals]}")

        return fired

    except Exception as e:
        print(f"Alert firing failed: {e}")
        return 0

def _send_confirmation(email: str, symbols: List[str]):
    """Send subscription confirmation email."""
    try:
        symbol_list = "".join([
            f'<div style="padding:6px 0;border-bottom:1px solid rgba(255,255,255,0.05);color:rgba(255,255,255,0.7);font-size:13px;">✓ {s}</div>'
            for s in symbols
        ])
        html = f"""
        <div style="background:#0a0a0f;padding:32px;font-family:monospace;max-width:600px;margin:0 auto;">
          <div style="border-bottom:1px solid rgba(255,255,255,0.08);padding-bottom:16px;margin-bottom:24px;">
            <span style="color:#00ff88;font-size:11px;font-weight:700;letter-spacing:0.15em;">● QUANTSIGNAL</span>
          </div>
          <h2 style="color:#ffffff;font-size:20px;margin:0 0 8px 0;">Alert subscription confirmed</h2>
          <p style="color:rgba(255,255,255,0.4);font-size:12px;margin:0 0 24px 0;">
            You'll receive an email when BUY or SELL signals fire for:
          </p>
          <div style="background:rgba(0,255,136,0.04);border:1px solid rgba(0,255,136,0.15);border-radius:10px;padding:16px;margin-bottom:32px;">
            {symbol_list}
          </div>
          <p style="color:rgba(255,255,255,0.3);font-size:11px;">Signals refresh daily at 6 AM IST.</p>
          <div style="border-top:1px solid rgba(255,255,255,0.06);padding-top:16px;margin-top:24px;">
            <a href="https://quantsignal-web.vercel.app/dashboard" style="color:#00ff88;font-size:11px;text-decoration:none;">→ Open Dashboard</a>
          </div>
          <p style="color:rgba(255,255,255,0.15);font-size:10px;margin-top:24px;">
            QuantSignal · Not financial advice
          </p>
        </div>
        """
        resend.Emails.send({
            "from": "QuantSignal <onboarding@resend.dev>",
            "to": [email],
            "subject": f"✅ Alert set for {', '.join(symbols[:3])}{'...' if len(symbols) > 3 else ''}",
            "html": html,
        })
    except Exception as e:
        print(f"Confirmation email failed: {e}")

def _send_signal_alert(email: str, signals: list) -> bool:
    """Send signal change alert email."""
    try:
        def currency(sig):
            return "₹" if sig["type"] == "IN_STOCK" else "$"

        cards = ""
        for s in signals:
            color = "#00ff88" if s["direction"] == "BUY" else "#ff4466"
            bg = "rgba(0,255,136,0.06)" if s["direction"] == "BUY" else "rgba(255,68,102,0.06)"
            border = "rgba(0,255,136,0.2)" if s["direction"] == "BUY" else "rgba(255,68,102,0.2)"
            arrow = "↑" if s["direction"] == "BUY" else "↓"
            cur = currency(s)
            cards += f"""
            <div style="background:{bg};border:1px solid {border};border-radius:12px;padding:16px;margin-bottom:12px;">
              <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
                <div>
                  <span style="color:#fff;font-size:14px;font-weight:700;">{s['display']}</span>
                  <span style="color:rgba(255,255,255,0.3);font-size:11px;margin-left:8px;">{s['name']}</span>
                </div>
                <span style="color:{color};font-size:18px;font-weight:800;">{arrow} {s['direction']}</span>
              </div>
              <div style="display:flex;gap:16px;">
                <div><span style="color:rgba(255,255,255,0.3);font-size:10px;">PRICE</span><br><span style="color:#fff;font-size:13px;font-weight:700;">{cur}{s['current_price']:,.2f}</span></div>
                <div><span style="color:rgba(255,255,255,0.3);font-size:10px;">PROBABILITY</span><br><span style="color:{color};font-size:13px;font-weight:700;">{s['probability']:.0%}</span></div>
                <div><span style="color:rgba(255,255,255,0.3);font-size:10px;">CONFIDENCE</span><br><span style="color:#fff;font-size:13px;font-weight:700;">{s['confidence']}</span></div>
              </div>
            </div>"""

        subject_assets = ", ".join([s["display"] for s in signals[:2]])
        if len(signals) > 2:
            subject_assets += f" +{len(signals)-2} more"

        html = f"""
        <div style="background:#0a0a0f;padding:32px;font-family:monospace;max-width:600px;margin:0 auto;">
          <div style="border-bottom:1px solid rgba(255,255,255,0.08);padding-bottom:16px;margin-bottom:24px;">
            <span style="color:#00ff88;font-size:11px;font-weight:700;letter-spacing:0.15em;">● QUANTSIGNAL SIGNAL ALERT</span>
          </div>
          <h2 style="color:#ffffff;font-size:20px;margin:0 0 8px 0;">New signal{"s" if len(signals)>1 else ""} fired</h2>
          <p style="color:rgba(255,255,255,0.4);font-size:12px;margin:0 0 24px 0;">
            {len(signals)} asset{"s" if len(signals)>1 else ""} on your watchlist {"have" if len(signals)>1 else "has"} a new signal
          </p>
          {cards}
          <div style="text-align:center;margin:32px 0;">
            <a href="https://quantsignal-web.vercel.app/dashboard" 
               style="background:#00ff88;color:#000;padding:12px 32px;border-radius:8px;text-decoration:none;font-size:12px;font-weight:800;letter-spacing:0.08em;">
              VIEW FULL ANALYSIS →
            </a>
          </div>
          <div style="border-top:1px solid rgba(255,255,255,0.06);padding-top:16px;">
            <p style="color:rgba(255,255,255,0.15);font-size:10px;margin:0;">
              QuantSignal · Educational signals only · Not financial advice<br>
              <a href="https://quantsignal-web.vercel.app/alerts/unsubscribe?email={email}" style="color:rgba(255,255,255,0.15);">Unsubscribe</a>
            </p>
          </div>
        </div>
        """

        resend.Emails.send({
            "from": "QuantSignal <onboarding@resend.dev>",
            "to": [email],
            "subject": f"🚨 Signal Alert: {subject_assets}",
            "html": html,
        })
        return True
    except Exception as e:
        print(f"Signal alert email failed: {e}")
        return False
