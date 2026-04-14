"""
agents/risk_agent.py
RiskAgent — monitors for dangerous signal patterns and portfolio risk.
Fires circuit breaker alerts when conditions are extreme.
"""
import logging
import os
from datetime import datetime, timezone

log = logging.getLogger(__name__)

CIRCUIT_BREAKER_RULES = [
    {"name": "low_confidence_flood",  "desc": "5+ signals all below 25% confidence"},
    {"name": "all_suppressed",        "desc": "All signals regime-suppressed"},
    {"name": "energy_all_exhausted",  "desc": "All scanned symbols show exhausted energy"},
    {"name": "ev_negative_majority",  "desc": "Majority of signals have negative EV"},
]


def run(signals: list[dict] = None) -> dict:
    """
    Analyze recent signals for risk patterns.
    Returns risk assessment — never raises.
    """
    result = {
        "agent":            "RiskAgent",
        "run_at":           datetime.now(timezone.utc).isoformat(),
        "circuit_breaker":  False,
        "triggered_rules":  [],
        "risk_level":       "normal",
        "warnings":         [],
    }

    if not signals:
        try:
            from supabase import create_client
            sb = create_client(
                os.environ.get("SUPABASE_URL", ""),
                os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_ANON_KEY", "")
            )
            res = sb.table("signal_context") \
                .select("symbol,direction,ev_score,energy_state,conflict_detected") \
                .limit(20).execute()
            signals = res.data or []
        except Exception as e:
            log.debug(f"[RiskAgent] signal fetch failed: {e}")
            return result

    if not signals:
        return result

    total     = len(signals)
    low_ev    = sum(1 for s in signals if (s.get("ev_score") or 0) < 0)
    exhausted = sum(1 for s in signals if s.get("energy_state") == "exhausted")
    conflicts = sum(1 for s in signals if s.get("conflict_detected"))

    # Evaluate rules
    if low_ev > total * 0.6:
        result["triggered_rules"].append("ev_negative_majority")
        result["warnings"].append(f"{low_ev}/{total} signals have negative EV — market edge degraded")

    if exhausted > total * 0.7:
        result["triggered_rules"].append("energy_all_exhausted")
        result["warnings"].append(f"{exhausted}/{total} symbols show exhausted energy — mean reversion risk high")

    if conflicts > total * 0.3:
        result["warnings"].append(f"{conflicts} signal conflicts detected — trade with caution")

    # Set risk level
    n = len(result["triggered_rules"])
    if n >= 2:
        result["circuit_breaker"] = True
        result["risk_level"]      = "critical"
    elif n == 1:
        result["risk_level"] = "elevated"
    else:
        result["risk_level"] = "normal"

    _store(result)
    return result


def _store(result: dict):
    try:
        from supabase import create_client
        sb = create_client(
            os.environ.get("SUPABASE_URL", ""),
            os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_ANON_KEY", "")
        )
        sb.table("agent_runs").upsert({
            "agent":    "RiskAgent",
            "run_at":   result["run_at"],
            "findings": result,
        }).execute()
    except Exception as e:
        log.debug(f"[RiskAgent] store failed: {e}")
