"""
Circuit Breaker — pauses signal alerts when system is losing consistently.
Checks drawdown and consecutive losses. Never crashes. Always fails open.
"""
import os, logging
from datetime import datetime, timezone, timedelta

log = logging.getLogger(__name__)

DRAWDOWN_THRESHOLD = -15.0   # pause if cumulative PnL < -15% over last 20 signals
CONSECUTIVE_LOSSES = 7        # pause if 7+ consecutive losses
COOLDOWN_HOURS = 12           # how long to stay paused

_breaker_state = {
    "active": False,
    "reason": None,
    "activated_at": None,
    "resume_at": None,
}

def check_circuit_breaker() -> dict:
    """
    Returns {"active": bool, "reason": str, "resume_at": str|None}
    Fails open (returns active=False) if data unavailable.
    """
    global _breaker_state

    # Check if cooldown has expired
    if _breaker_state["active"] and _breaker_state["resume_at"]:
        if datetime.now(timezone.utc) > _breaker_state["resume_at"]:
            log.info("[circuit_breaker] cooldown expired — resetting")
            _reset()

    if _breaker_state["active"]:
        return {
            "active": True,
            "reason": _breaker_state["reason"],
            "resume_at": _breaker_state["resume_at"].isoformat() if _breaker_state["resume_at"] else None,
        }

    try:
        outcomes = _get_recent_outcomes(limit=20)

        if len(outcomes) < 10:
            return {"active": False, "reason": "insufficient_data"}

        # Check cumulative drawdown
        total_pnl = sum(o["pnl"] for o in outcomes if o["pnl"] is not None)
        if total_pnl < DRAWDOWN_THRESHOLD:
            return _activate(f"drawdown_{abs(total_pnl):.1f}pct_over_last_{len(outcomes)}_signals")

        # Check consecutive losses from most recent
        consecutive = 0
        for o in outcomes:
            if o["outcome"] == "loss":
                consecutive += 1
            else:
                break

        if consecutive >= CONSECUTIVE_LOSSES:
            return _activate(f"{consecutive}_consecutive_losses")

        return {
            "active": False,
            "stats": {
                "recent_pnl": round(total_pnl, 2),
                "consecutive_losses": consecutive,
                "signals_checked": len(outcomes),
            }
        }

    except Exception as e:
        log.warning(f"[circuit_breaker] check failed — failing open: {e}")
        return {"active": False, "reason": "check_failed"}

def _get_recent_outcomes(limit: int = 20) -> list:
    """Fetch recent evaluated signals from DB."""
    import psycopg2
    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        return []

    con = psycopg2.connect(db_url)
    cur = con.cursor()
    cur.execute("""
        SELECT outcome,
               CASE
                   WHEN direction='BUY' AND exit_price > 0 AND entry_price > 0
                       THEN (exit_price - entry_price) / entry_price * 100
                   WHEN direction='SELL' AND exit_price > 0 AND entry_price > 0
                       THEN (entry_price - exit_price) / entry_price * 100
                   ELSE NULL
               END as pnl
        FROM signal_history
        WHERE outcome IS NOT NULL
        ORDER BY evaluated_at DESC
        LIMIT %s
    """, (limit,))
    rows = cur.fetchall()
    con.close()
    return [{"outcome": r[0], "pnl": r[1]} for r in rows]

def _activate(reason: str) -> dict:
    global _breaker_state
    now = datetime.now(timezone.utc)
    resume = now + timedelta(hours=COOLDOWN_HOURS)
    _breaker_state = {
        "active": True,
        "reason": reason,
        "activated_at": now,
        "resume_at": resume,
    }
    log.error(f"[circuit_breaker] ACTIVATED — reason: {reason} — resume: {resume.isoformat()}")
    try:
        from app.domain.core.error_logger import log_error
        log_error("circuit_breaker", "breaker_activated", message=reason,
                  context={"resume_at": resume.isoformat()})
    except Exception:
        pass
    return {
        "active": True,
        "reason": reason,
        "resume_at": resume.isoformat(),
    }

def _reset():
    global _breaker_state
    _breaker_state = {"active": False, "reason": None, "activated_at": None, "resume_at": None}

def get_breaker_status() -> dict:
    return check_circuit_breaker()
