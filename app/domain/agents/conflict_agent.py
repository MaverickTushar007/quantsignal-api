"""
agents/conflict_agent.py
ConflictAgent — scans all signals every cron cycle.
Detects when ML direction disagrees with regime + energy state.
Produces a market-wide "conflict score" that Perseus uses as a stress indicator.
Conflicts are stored and fed into BriefingAgent commentary.
"""
import logging
import os
from datetime import datetime, timezone

log = logging.getLogger(__name__)

# Regime → expected ML direction
REGIME_EXPECTED = {
    "bull":     "BUY",
    "trending": "BUY",
    "bear":     "SELL",
    "ranging":  None,   # either is fine
    "unknown":  None,
}

# Energy → expected direction modifier
ENERGY_CONFLICTS = {
    # energy_state: directions that conflict with it
    "exhausted": ["BUY"],    # exhausted energy + BUY = conflict
    "building":  ["SELL"],   # building energy + SELL = conflict
    "releasing": [],         # neutral
    "neutral":   [],
    "unknown":   [],
}


def run(symbols: list[str] | None = None) -> dict:
    """
    Full conflict scan across the market.
    Returns conflict map + market stress score.
    Never raises.
    """
    result = {
        "agent":          "ConflictAgent",
        "run_at":         datetime.now(timezone.utc).isoformat(),
        "conflicts":      [],
        "clean":          [],
        "conflict_score": 0.0,
        "stress_level":   "low",
        "summary":        "",
    }

    try:
        # Load signals from cache
        import json
        from app.core.config import BASE_DIR
        all_signals = []
        # Try Redis/Upstash first
        try:
            from app.infrastructure.cache.cache import get_cached
            cached = get_cached("signals_cache")
            if cached and isinstance(cached, dict):
                all_signals = list(cached.values())
        except Exception:
            pass
        # Fall back to local JSON cache file
        if not all_signals:
            try:
                cache_path = BASE_DIR / "data/signals_cache.json"
                if cache_path.exists():
                    raw = json.loads(cache_path.read_text())
                    all_signals = list(raw.values()) if isinstance(raw, dict) else raw
            except Exception:
                pass

        if not all_signals:
            result["summary"] = "No cached signals available."
            _store(result)
            return result

        if symbols:
            all_signals = [s for s in all_signals if s.get("symbol") in symbols]

        conflicts = []
        clean = []

        for sig in all_signals:
            sym       = sig.get("symbol", "")
            direction = sig.get("direction", "HOLD")
            regime    = sig.get("regime", "unknown")
            energy    = sig.get("energy_state", "unknown")
            prob      = sig.get("probability", 0)
            ev        = sig.get("ev_score") or 0

            if direction == "HOLD":
                continue  # HOLDs don't conflict by definition

            conflict_reasons = []

            # Check regime conflict
            expected_dir = REGIME_EXPECTED.get(regime)
            if expected_dir and direction != expected_dir:
                conflict_reasons.append(
                    f"ML={direction} but regime={regime} expects {expected_dir}"
                )

            # Check energy conflict
            energy_bad_dirs = ENERGY_CONFLICTS.get(energy, [])
            if direction in energy_bad_dirs:
                conflict_reasons.append(
                    f"ML={direction} but energy={energy} signals opposite"
                )

            entry = {
                "symbol":    sym,
                "direction": direction,
                "regime":    regime,
                "energy":    energy,
                "prob":      round(prob, 3),
                "ev":        round(ev, 3),
            }

            if conflict_reasons:
                entry["reasons"] = conflict_reasons
                entry["severity"] = _severity(prob, len(conflict_reasons))
                conflicts.append(entry)
            else:
                clean.append(entry)

        result["conflicts"] = conflicts
        result["clean"]     = [c["symbol"] for c in clean]

        # Conflict score: 0.0 (no conflict) → 1.0 (all signals conflicting)
        total = len(conflicts) + len(clean)
        if total > 0:
            result["conflict_score"] = round(len(conflicts) / total, 3)

        score = result["conflict_score"]
        if score >= 0.60:
            result["stress_level"] = "high"
        elif score >= 0.35:
            result["stress_level"] = "elevated"
        else:
            result["stress_level"] = "low"

        # Summary for Perseus + BriefingAgent
        high_sev = [c for c in conflicts if c.get("severity") == "high"]
        result["summary"] = (
            f"{len(conflicts)}/{total} signals have ML vs regime/energy conflicts. "
            f"Conflict score: {score:.0%} — stress level: {result['stress_level'].upper()}. "
            f"High-severity conflicts: {', '.join(c['symbol'] for c in high_sev) or 'none'}."
        )

    except Exception as e:
        log.warning(f"[ConflictAgent] failed: {e}")
        result["summary"] = f"ConflictAgent error: {e}"

    _store(result)
    return result


def _severity(prob: float, n_reasons: int) -> str:
    """High severity = high-confidence signal that conflicts strongly."""
    if prob >= 0.55 and n_reasons >= 2:
        return "high"
    if prob >= 0.45 or n_reasons >= 2:
        return "medium"
    return "low"


def get_conflict_map() -> dict[str, dict]:
    """
    Return latest conflict data keyed by symbol.
    Used by Perseus context builder.
    """
    try:
        from supabase import create_client
        sb = create_client(
            os.environ.get("SUPABASE_URL", ""),
            os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_ANON_KEY", "")
        )
        res = sb.table("agent_runs") \
            .select("findings") \
            .eq("agent", "ConflictAgent") \
            .order("run_at", desc=True).limit(1).execute()
        if res.data:
            findings = res.data[0].get("findings", {})
            return {
                c["symbol"]: c
                for c in findings.get("conflicts", [])
            }
    except Exception:
        pass
    return {}


def _store(result: dict):
    try:
        from supabase import create_client
        sb = create_client(
            os.environ.get("SUPABASE_URL", ""),
            os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_ANON_KEY", "")
        )
        sb.table("agent_runs").upsert({
            "agent":    "ConflictAgent",
            "run_at":   result["run_at"],
            "findings": result,
        }).execute()
    except Exception as e:
        log.debug(f"[ConflictAgent] store failed: {e}")
