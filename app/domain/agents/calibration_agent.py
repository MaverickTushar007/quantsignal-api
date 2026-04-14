"""
agents/calibration_agent.py
CalibrationAgent — runs weekly (or on-demand).
Reads guardian_outcomes table, computes per-symbol win rates,
and writes calibrated thresholds back to Supabase so GuardianAgent
automatically tightens/loosens alert criteria per symbol.
"""
import logging
import os
from datetime import datetime, timezone, timedelta
from collections import defaultdict

log = logging.getLogger(__name__)

# Default thresholds if no outcome history exists yet
DEFAULT_PROB_THRESHOLD = 0.45
DEFAULT_EV_MINIMUM = 0.10

# Minimum outcomes needed before we trust calibration
MIN_SAMPLES = 5


def run() -> dict:
    """
    Full calibration cycle.
    1. Load all guardian_outcomes
    2. Compute per-symbol win rate + avg EV
    3. Write calibrated thresholds to calibration_config table
    4. Return summary
    Never raises.
    """
    result = {
        "agent":       "CalibrationAgent",
        "run_at":      datetime.now(timezone.utc).isoformat(),
        "calibrated":  {},
        "skipped":     [],
        "global":      {},
        "summary":     "",
    }

    try:
        from supabase import create_client
        sb = create_client(
            os.environ.get("SUPABASE_URL", ""),
            os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_ANON_KEY", "")
        )

        # Pull all outcomes (last 90 days)
        cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
        res = sb.table("guardian_outcomes") \
            .select("symbol,direction,prob,ev,correct,window") \
            .gte("evaluated_at", cutoff) \
            .execute()

        outcomes = res.data or []
        if not outcomes:
            result["summary"] = "No outcome history yet — using default thresholds."
            _store(sb, result)
            return result

        # Group by symbol
        by_symbol: dict[str, list] = defaultdict(list)
        for o in outcomes:
            by_symbol[o["symbol"]].append(o)

        # Global stats
        all_correct = [o for o in outcomes if o.get("correct")]
        global_win_rate = len(all_correct) / len(outcomes) if outcomes else 0
        result["global"] = {
            "total_outcomes": len(outcomes),
            "win_rate": round(global_win_rate, 3),
            "symbols_tracked": len(by_symbol),
        }

        # Per-symbol calibration
        calibrated = {}
        for sym, records in by_symbol.items():
            if len(records) < MIN_SAMPLES:
                result["skipped"].append(f"{sym} ({len(records)} samples < {MIN_SAMPLES} min)")
                continue

            wins = sum(1 for r in records if r.get("correct"))
            win_rate = wins / len(records)
            avg_ev = sum(r.get("ev") or 0 for r in records) / len(records)
            avg_prob = sum(r.get("prob") or 0 for r in records) / len(records)

            # Calibration logic:
            # - If win rate > 60%: relax threshold (reward good signals)
            # - If win rate < 40%: tighten threshold (penalise bad signals)
            # - If win rate 40-60%: use default
            if win_rate >= 0.60:
                prob_threshold = max(0.35, DEFAULT_PROB_THRESHOLD - 0.05)
                ev_minimum = max(0.05, DEFAULT_EV_MINIMUM - 0.05)
                tier = "relaxed"
            elif win_rate <= 0.40:
                prob_threshold = min(0.65, DEFAULT_PROB_THRESHOLD + 0.10)
                ev_minimum = min(0.30, DEFAULT_EV_MINIMUM + 0.10)
                tier = "tightened"
            else:
                prob_threshold = DEFAULT_PROB_THRESHOLD
                ev_minimum = DEFAULT_EV_MINIMUM
                tier = "default"

            calibrated[sym] = {
                "symbol":          sym,
                "win_rate":        round(win_rate, 3),
                "avg_ev":          round(avg_ev, 4),
                "avg_prob":        round(avg_prob, 3),
                "samples":         len(records),
                "prob_threshold":  round(prob_threshold, 3),
                "ev_minimum":      round(ev_minimum, 3),
                "tier":            tier,
                "calibrated_at":   result["run_at"],
            }

            # Upsert into calibration_config table
            try:
                sb.table("calibration_config").upsert({
                    "symbol":          sym,
                    "prob_threshold":  round(prob_threshold, 3),
                    "ev_minimum":      round(ev_minimum, 3),
                    "win_rate":        round(win_rate, 3),
                    "samples":         len(records),
                    "tier":            tier,
                    "calibrated_at":   result["run_at"],
                }).execute()
            except Exception as e:
                log.debug(f"[CalibrationAgent] upsert {sym} failed: {e}")

        result["calibrated"] = calibrated

        n_tight = sum(1 for c in calibrated.values() if c["tier"] == "tightened")
        n_relax = sum(1 for c in calibrated.values() if c["tier"] == "relaxed")
        result["summary"] = (
            f"Calibrated {len(calibrated)} symbols from {len(outcomes)} outcomes. "
            f"Global win rate: {global_win_rate:.0%}. "
            f"Tightened: {n_tight}, Relaxed: {n_relax}, Default: {len(calibrated)-n_tight-n_relax}."
        )

    except Exception as e:
        log.warning(f"[CalibrationAgent] failed: {e}")
        result["summary"] = f"Calibration failed: {e}"

    _store_run(result)
    return result


def get_threshold(symbol: str) -> tuple[float, float]:
    """
    Get calibrated (prob_threshold, ev_minimum) for a symbol.
    Falls back to defaults if no calibration exists.
    Call this from GuardianAgent instead of hardcoded values.
    """
    try:
        from supabase import create_client
        sb = create_client(
            os.environ.get("SUPABASE_URL", ""),
            os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_ANON_KEY", "")
        )
        res = sb.table("calibration_config") \
            .select("prob_threshold,ev_minimum") \
            .eq("symbol", symbol) \
            .limit(1).execute()
        if res.data:
            row = res.data[0]
            return float(row["prob_threshold"]), float(row["ev_minimum"])
    except Exception:
        pass
    return DEFAULT_PROB_THRESHOLD, DEFAULT_EV_MINIMUM


def _store_run(result: dict):
    try:
        from supabase import create_client
        sb = create_client(
            os.environ.get("SUPABASE_URL", ""),
            os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_ANON_KEY", "")
        )
        _store(sb, result)
    except Exception as e:
        log.debug(f"[CalibrationAgent] store_run failed: {e}")


def _store(sb, result: dict):
    try:
        sb.table("agent_runs").upsert({
            "agent":    "CalibrationAgent",
            "run_at":   result["run_at"],
            "findings": result,
        }).execute()
    except Exception as e:
        log.debug(f"[CalibrationAgent] store failed: {e}")
