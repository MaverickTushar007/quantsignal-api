"""
Signal Context Generator — generates reasoning text for every signal.
Stores context in Supabase signal_context table.
Runs at signal generation time, non-blocking.
"""
import os, logging
log = logging.getLogger(__name__)

def generate_signal_context(sig: dict) -> dict:
    """
    Generate and store context for a signal.
    Returns context dict. Never raises.
    """
    try:
        symbol    = sig.get("symbol", "")
        direction = sig.get("direction", "HOLD")
        regime    = sig.get("regime", "unknown")
        prob      = sig.get("probability", 0)
        ev_score  = sig.get("ev_score")
        conf      = sig.get("confidence", "")

        # Pull recent history for this symbol
        history   = _get_symbol_history(symbol, limit=5)
        regime_stats = _get_regime_stats(regime, direction)

        # Build context text
        lines = []
        lines.append(f"{symbol} {direction} signal in {regime} regime.")
        lines.append(f"Calibrated probability: {prob:.1%} | Confidence: {conf}")

        if ev_score is not None:
            lines.append(f"Expected value: {ev_score:+.2f}% per trade in this regime/direction.")

        if regime_stats:
            wr  = regime_stats.get("win_rate")
            tot = regime_stats.get("total", 0)
            if wr is not None and tot >= 5:
                lines.append(
                    f"Historical base rate: {wr:.1%} win rate over {tot} trades "
                    f"({regime} {direction})."
                )

        if history:
            outcomes = [h["outcome"] for h in history]
            pnls     = [h["pnl"] for h in history if h["pnl"] is not None]
            lines.append(
                f"Last {len(history)} {symbol} signals: "
                f"{', '.join(outcomes)}."
            )
            if pnls:
                avg_pnl = sum(pnls) / len(pnls)
                lines.append(f"Avg PnL on recent {symbol} trades: {avg_pnl:+.2f}%.")

        conflict = _detect_conflict(sig, history)
        context_text = " ".join(lines)

        # Store in Supabase
        _store_context(symbol, direction, context_text, ev_score, conflict)

        return {
            "context_text": context_text,
            "conflict_detected": conflict["detected"],
            "conflict_reason": conflict.get("reason"),
        }

    except Exception as e:
        log.warning(f"[context_generator] failed for {sig.get('symbol')}: {e}")
        return {}


def _get_symbol_history(symbol: str, limit: int = 5) -> list:
    try:
        import psycopg2
        con = psycopg2.connect(os.environ["DATABASE_URL"])
        cur = con.cursor()
        cur.execute("""
            SELECT outcome, direction,
                   CASE
                       WHEN direction='BUY'  AND exit_price>0 AND entry_price>0
                           THEN (exit_price - entry_price)/entry_price*100
                       WHEN direction='SELL' AND exit_price>0 AND entry_price>0
                           THEN (entry_price - exit_price)/entry_price*100
                       ELSE NULL
                   END as pnl
            FROM signal_history
            WHERE symbol=%s AND outcome IS NOT NULL
            ORDER BY evaluated_at DESC LIMIT %s
        """, (symbol, limit))
        rows = cur.fetchall()
        con.close()
        return [{"outcome": r[0], "direction": r[1], "pnl": r[2]} for r in rows]
    except Exception as e:
        log.debug(f"[context_generator] history fetch failed: {e}")
        return []


def _get_regime_stats(regime: str, direction: str) -> dict:
    try:
        from app.domain.core.ev_calculator import get_ev_stats
        stats = get_ev_stats()
        key   = (regime, direction)
        if key in stats:
            s = stats[key]
            return {
                "win_rate": s.get("win_rate"),
                "ev":       s.get("ev"),
                "total":    s.get("total", 0),
            }
    except Exception as e:
        log.debug(f"[context_generator] regime stats failed: {e}")
    return {}


def _detect_conflict(sig: dict, history: list) -> dict:
    """Detect if current signal conflicts with recent history."""
    if not history:
        return {"detected": False}

    direction = sig.get("direction", "HOLD")
    if direction == "HOLD":
        return {"detected": False}

    # Conflict: last signal was opposite direction and was a win
    last = history[0]
    if last["direction"] != direction and last["outcome"] == "win":
        return {
            "detected": True,
            "reason": f"Last signal was {last['direction']} (WIN) — current is {direction}",
        }

    # Conflict: 3+ consecutive losses on this symbol
    if len(history) >= 3:
        recent_outcomes = [h["outcome"] for h in history[:3]]
        if all(o == "loss" for o in recent_outcomes):
            return {
                "detected": True,
                "reason": f"3 consecutive losses on {sig.get('symbol')} — elevated risk",
            }

    return {"detected": False}


def _store_context(symbol, direction, context_text, ev_score, conflict):
    try:
        from supabase import create_client
        sb = create_client(
            os.environ["SUPABASE_URL"],
            os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_ANON_KEY", "")
        )
        sb.table("signal_context").insert({
            "symbol":            symbol,
            "direction":         direction,
            "context_text":      context_text,
            "ev_score":          ev_score,
            "conflict_detected": conflict["detected"],
            "conflict_reason":   conflict.get("reason"),
        }).execute()
    except Exception as e:
        log.debug(f"[context_generator] store failed: {e}")
