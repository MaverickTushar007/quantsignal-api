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

        # Generate LLM interpretation (2-sentence signal summary)
        interpretation = _generate_interpretation(context_text, sig)
        if interpretation:
            context_text = interpretation

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




SIGNAL_SYSTEM_PROMPT = """You are a quant signal interpreter. Given a signal and its context, generate exactly 2 sentences that explain:
1. Why this signal is firing (regime, energy state, confluence factors)
2. What the historical base rate suggests about this setup
Be factual and specific with numbers. Never say "buy" or "sell" directly. State evidence only. No fluff."""

def _generate_interpretation(context_text: str, sig: dict) -> str:
    """
    Generate 2-sentence signal interpretation.
    Tries: Groq → OpenRouter → template fallback.
    Never returns empty string.
    """
    import os
    prompt_user = f"""Signal context:
{context_text}

Additional context:
- Energy state: {sig.get("energy_state", "unknown")} ({sig.get("energy_reason", "")})
- EV score: {sig.get("ev_score")}
- Confluence: {sig.get("confluence_score")}
- Raw probability: {sig.get("raw_probability")}
- Final probability: {sig.get("probability")}

Generate 2 sentences interpreting this signal."""

    # 1. Try Groq
    try:
        import groq
        key = os.environ.get("GROQ_API_KEY", "")
        if key:
            client = groq.Groq(api_key=key)
            resp = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {"role": "system", "content": SIGNAL_SYSTEM_PROMPT},
                    {"role": "user",   "content": prompt_user},
                ],
                max_tokens=120,
                temperature=0.2,
            )
            result = resp.choices[0].message.content.strip()
            if result:
                log.debug("[context_generator] interpretation via Groq")
                return result
    except Exception as e:
        log.debug(f"[context_generator] Groq failed: {e}")

    # 2. Try OpenRouter (free tier — no cost)
    try:
        import httpx
        or_key = os.environ.get("OPENROUTER_API_KEY", "")
        if or_key:
            r = httpx.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {or_key}", "Content-Type": "application/json"},
                json={
                    "model": "meta-llama/llama-3.3-8b-instruct:free",
                    "messages": [
                        {"role": "system", "content": SIGNAL_SYSTEM_PROMPT},
                        {"role": "user",   "content": prompt_user},
                    ],
                    "max_tokens": 120,
                },
                timeout=8,
            )
            result = r.json()["choices"][0]["message"]["content"].strip()
            if result:
                log.debug("[context_generator] interpretation via OpenRouter")
                return result
    except Exception as e:
        log.debug(f"[context_generator] OpenRouter failed: {e}")

    # 3. Template fallback — always works, never empty
    symbol    = sig.get("symbol", "")
    direction = sig.get("direction", "HOLD")
    regime    = sig.get("regime", "unknown")
    prob      = sig.get("probability", 0)
    ev        = sig.get("ev_score")
    energy    = sig.get("energy_state", "unknown")
    conf      = sig.get("confluence_score", "?")

    ev_str = f"EV +{ev:.2f}%" if ev and ev > 0 else (f"EV {ev:.2f}%" if ev else "EV unavailable")
    energy_str = {"exhausted": "market is overextended — mean reversion risk elevated",
                  "coiled":    "market energy compressed — breakout likely imminent",
                  "releasing": "momentum active — trend confirmation in play"}.get(energy, "energy state neutral")

    return (
        f"{symbol} {direction} signal firing in {regime} regime with {prob:.0%} calibrated confidence "
        f"and {conf} confluence score; {energy_str}. "
        f"{ev_str} based on historical outcomes — "
        f"{'edge present' if ev and ev > 0 else 'edge marginal, trade with caution'}."
    )

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
