"""
agents/outcome_agent.py
OutcomeAgent — checks Guardian alert history and records whether signals were right.
Runs every cron cycle. Feeds accuracy data back into calibration.
"""
import logging
import os
from datetime import datetime, timezone, timedelta

log = logging.getLogger(__name__)


def run() -> dict:
    """
    Check Guardian alerts from 24h and 72h ago.
    Compare predicted direction vs actual price move.
    Store outcomes for calibration.
    Never raises.
    """
    result = {
        "agent":      "OutcomeAgent",
        "run_at":     datetime.now(timezone.utc).isoformat(),
        "evaluated":  [],
        "accuracy":   {},
    }

    try:
        from supabase import create_client
        sb = create_client(
            os.environ.get("SUPABASE_URL", ""),
            os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_ANON_KEY", "")
        )

        now     = datetime.now(timezone.utc)
        cutoffs = [("24h", now - timedelta(hours=24)), ("72h", now - timedelta(hours=72))]

        for label, cutoff in cutoffs:
            window_start = cutoff - timedelta(hours=2)
            window_end   = cutoff + timedelta(hours=2)

            # Find Guardian runs in this window that had alerts
            res = sb.table("agent_runs") \
                .select("findings,run_at") \
                .eq("agent", "GuardianAgent") \
                .gte("run_at", window_start.isoformat()) \
                .lte("run_at", window_end.isoformat()) \
                .execute()

            for row in (res.data or []):
                findings = row.get("findings", {})
                for alert in findings.get("alerts_fired", []):
                    outcome = _evaluate_outcome(alert, now)
                    if outcome:
                        outcome["window"] = label
                        result["evaluated"].append(outcome)
                        _store_outcome(sb, outcome)

        # Compute accuracy
        if result["evaluated"]:
            wins  = sum(1 for e in result["evaluated"] if e.get("correct"))
            total = len(result["evaluated"])
            result["accuracy"] = {
                "total":    total,
                "correct":  wins,
                "win_rate": f"{wins/total:.0%}",
            }

    except Exception as e:
        log.warning(f"[OutcomeAgent] failed: {e}")

    _store_run(result)
    return result


def _evaluate_outcome(alert: dict, now: datetime) -> dict | None:
    """Check if the predicted direction was correct by comparing price at alert vs now."""
    try:
        import yfinance as yf
        sym       = alert.get("symbol")
        direction = alert.get("direction")
        if not sym or direction == "HOLD":
            return None

        ticker  = yf.Ticker(sym)
        hist    = ticker.history(period="5d", interval="1h")
        if hist.empty:
            return None

        # Price now vs price at alert time (approximate — use earliest available)
        price_then = float(hist["Close"].iloc[0])
        price_now  = float(hist["Close"].iloc[-1])
        pct_move   = (price_now - price_then) / price_then * 100

        correct = (direction == "BUY" and pct_move > 0) or \
                  (direction == "SELL" and pct_move < 0)

        return {
            "symbol":     sym,
            "direction":  direction,
            "prob":       alert.get("prob"),
            "ev":         alert.get("ev"),
            "price_then": round(price_then, 4),
            "price_now":  round(price_now, 4),
            "pct_move":   round(pct_move, 3),
            "correct":    correct,
            "evaluated_at": now.isoformat(),
        }
    except Exception as e:
        log.debug(f"[OutcomeAgent] evaluate {alert.get('symbol')} failed: {e}")
        return None


def _store_outcome(sb, outcome: dict):
    """Store individual outcome in guardian_outcomes table."""
    try:
        sb.table("guardian_outcomes").insert({
            "symbol":       outcome["symbol"],
            "direction":    outcome["direction"],
            "prob":         outcome["prob"],
            "ev":           outcome["ev"],
            "pct_move":     outcome["pct_move"],
            "correct":      outcome["correct"],
            "window":       outcome["window"],
            "evaluated_at": outcome["evaluated_at"],
        }).execute()
    except Exception as e:
        log.debug(f"[OutcomeAgent] store outcome failed: {e}")


def _store_run(result: dict):
    try:
        from supabase import create_client
        sb = create_client(
            os.environ.get("SUPABASE_URL", ""),
            os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_ANON_KEY", "")
        )
        sb.table("agent_runs").upsert({
            "agent":    "OutcomeAgent",
            "run_at":   result["run_at"],
            "findings": result,
        }).execute()
    except Exception as e:
        log.debug(f"[OutcomeAgent] store run failed: {e}")
