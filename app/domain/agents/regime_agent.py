"""
agents/regime_agent.py
RegimeAgent — monitors regime transitions and energy state shifts.
Runs on demand or via cron. Stores findings to Supabase.
"""
import logging
import os
from datetime import datetime, timezone

log = logging.getLogger(__name__)


def run(symbols: list[str] = None) -> dict:
    """
    Scan symbols for regime/energy shifts.
    Returns findings dict — never raises.
    """
    from app.domain.signal.service import generate_signal
    from app.domain.signal.pipeline import enrich_signal

    if symbols is None:
        from app.domain.data.universe import TICKERS
        # Priority symbols always scanned first
        priority = ["NVDA", "BTC-USD", "RELIANCE.NS", "AAPL", "MSFT", "ETH-USD",
                    "TSLA", "^NSEI", "AMZN", "GOOGL", "META", "SOL-USD"]
        all_syms = [t["symbol"] for t in TICKERS]
        rest = [s for s in all_syms if s not in priority]
        symbols = priority + rest[:18]  # 12 priority + 18 others = 30 total

    findings = {
        "agent":        "RegimeAgent",
        "run_at":       datetime.now(timezone.utc).isoformat(),
        "alerts":       [],
        "regime_map":   {},
        "energy_map":   {},
        "symbols_scanned": len(symbols),
    }

    for sym in symbols:
        try:
            sig = generate_signal(sym, include_reasoning=False)
            if not sig:
                continue
            sig = enrich_signal(sig, sym)

            regime = sig.get("regime", "unknown")
            energy = sig.get("energy_state", "unknown")
            findings["regime_map"][sym] = regime
            findings["energy_map"][sym] = energy

            # Alert on high-conviction setups
            prob = sig.get("probability", 0)
            ev   = sig.get("ev_score") or 0
            if prob >= 0.55 and ev > 0.5:
                findings["alerts"].append({
                    "symbol":    sym,
                    "direction": sig.get("direction"),
                    "regime":    regime,
                    "energy":    energy,
                    "prob":      prob,
                    "ev":        ev,
                    "reason":    f"High conviction: {prob:.0%} prob, EV +{ev:.2f}%",
                })
        except Exception as e:
            log.debug(f"[RegimeAgent] {sym} failed: {e}")

    _store(findings)
    return findings


def _store(findings: dict):
    try:
        import os
        from supabase import create_client
        sb = create_client(
            os.environ.get("SUPABASE_URL", ""),
            os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_ANON_KEY", "")
        )
        sb.table("agent_runs").upsert({
            "agent":    "RegimeAgent",
            "run_at":   findings["run_at"],
            "findings": findings,
        }).execute()
    except Exception as e:
        log.debug(f"[RegimeAgent] store failed: {e}")
