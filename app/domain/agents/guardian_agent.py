"""
agents/guardian_agent.py
GuardianAgent — autonomous 15-minute monitor.
Watches watchlist symbols, fires Telegram alerts when conditions breach thresholds.
No human trigger needed — runs on cron.
"""
import logging
import os
from datetime import datetime, timezone

log = logging.getLogger(__name__)


def run(user_id: str = "default") -> dict:
    """
    Full autonomous monitoring cycle.
    1. Load user watchlist + thresholds
    2. Scan each symbol
    3. Fire alerts for anything that meets criteria
    4. Store findings
    Never raises.
    """
    result = {
        "agent":        "GuardianAgent",
        "user_id":      user_id,
        "run_at":       datetime.now(timezone.utc).isoformat(),
        "alerts_fired": [],
        "watched":      [],
        "skipped":      [],
        "risk_level":   "normal",
    }

    # Load user preferences
    try:
        from app.api.routes.preferences import _load_prefs
        prefs     = _load_prefs(user_id)
        watchlist = prefs.get("watchlist", [])
        threshold = prefs.get("alert_threshold", 0.50)
        ev_min    = prefs.get("ev_minimum", 0.0)
        suppress  = prefs.get("suppress_hold", True)
    except Exception as e:
        log.warning(f"[Guardian] prefs load failed: {e}")
        watchlist = ["NVDA", "BTC-USD", "RELIANCE.NS"]
        threshold = 0.50
        ev_min    = 0.0
        suppress  = True

    if not watchlist:
        result["note"] = "Watchlist empty — add symbols via PUT /preferences"
        return result

    # Pull latest RiskAgent findings
    try:
        from supabase import create_client
        sb = create_client(
            os.environ.get("SUPABASE_URL", ""),
            os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_ANON_KEY", "")
        )
        risk_res = sb.table("agent_runs").select("findings") \
            .eq("agent", "RiskAgent") \
            .order("run_at", desc=True).limit(1).execute()
        if risk_res.data:
            result["risk_level"] = risk_res.data[0]["findings"].get("risk_level", "normal")
    except Exception:
        pass

    # Scan each watchlist symbol
    from app.domain.signal.service import generate_signal
    from app.domain.signal.pipeline import enrich_signal

    for sym in watchlist:
        try:
            sig = generate_signal(sym, include_reasoning=False)
            if not sig:
                result["skipped"].append(sym)
                continue

            sig    = enrich_signal(sig, sym)
            prob   = sig.get("probability", 0)
            ev     = sig.get("ev_score") or 0
            direct = sig.get("direction", "HOLD")
            regime = sig.get("regime", "unknown")
            energy = sig.get("energy_state", "unknown")

            watched_entry = {
                "symbol":    sym,
                "direction": direct,
                "prob":      round(prob, 3),
                "ev":        round(ev, 3),
                "regime":    regime,
                "energy":    energy,
            }
            result["watched"].append(watched_entry)

            # Skip HOLDs if suppress is on
            if suppress and direct == "HOLD":
                continue

            # Check alert criteria
            meets_prob = prob >= threshold
            meets_ev   = ev >= ev_min
            is_conflict = sig.get("conflict_detected", False)

            if meets_prob and meets_ev:
                alert = {**watched_entry, "reason": f"prob={prob:.0%} ≥ threshold={threshold:.0%}, EV={ev:+.2f}%"}
                if is_conflict:
                    alert["warning"] = "⚠️ Signal conflict detected — trade with caution"
                result["alerts_fired"].append(alert)
                _fire_telegram(alert, result["risk_level"])

        except Exception as e:
            log.debug(f"[Guardian] {sym} scan failed: {e}")
            result["skipped"].append(sym)

    _store(result)
    return result


def _fire_telegram(alert: dict, risk_level: str):
    """Send Telegram alert for Guardian finding."""
    try:
        from app.domain.alerts.telegram import send_telegram
        from app.domain.alerts.dedup import should_alert

        sym = alert["symbol"]
        if not should_alert(sym):
            return  # Already alerted recently

        risk_emoji = "🚨" if risk_level == "critical" else "🛡️"
        direction_emoji = "📈" if alert["direction"] == "BUY" else "📉"

        msg = (
            f"{risk_emoji} *GuardianAlert* — {sym}\n"
            f"{direction_emoji} *{alert['direction']}* signal\n"
            f"├ Probability: {alert['prob']:.0%}\n"
            f"├ EV: {alert['ev']:+.2f}%\n"
            f"├ Regime: {alert['regime']} | Energy: {alert['energy']}\n"
            f"└ Risk level: {risk_level.upper()}\n"
        )
        if alert.get("warning"):
            msg += f"\n{alert['warning']}"

        send_telegram(msg)
    except Exception as e:
        log.debug(f"[Guardian] telegram failed: {e}")


def _store(result: dict):
    try:
        from supabase import create_client
        sb = create_client(
            os.environ.get("SUPABASE_URL", ""),
            os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_ANON_KEY", "")
        )
        sb.table("agent_runs").upsert({
            "agent":    "GuardianAgent",
            "run_at":   result["run_at"],
            "findings": result,
        }).execute()
    except Exception as e:
        log.debug(f"[Guardian] store failed: {e}")
