"""
signal/pipeline.py
Single responsibility: enrich a raw signal dict with regime, calibration,
energy, EV, context, and suppression logic.
Called by route handlers — keeps routes.py clean.
"""
import logging
import threading
log = logging.getLogger(__name__)


def enrich_signal(sig: dict, symbol: str) -> dict:
    """
    Full enrichment pipeline. Never raises — always returns sig.
    Steps: regime → calibration → energy → EV → suppression → context
    """

    # 1. REGIME
    try:
        from app.infrastructure.db.signal_history import _get_conn
        rc, _ = _get_conn()
        cur = rc.cursor()
        cur.execute(
            "SELECT regime, return_20d, signal_bias FROM regime_cache WHERE symbol=%s",
            (symbol,)
        )
        row = cur.fetchone()
        rc.close()
        if row:
            sig["regime"]            = row[0]
            sig["regime_return_20d"] = row[1]
            sig["signal_bias"]       = row[2]
        else:
            sig.setdefault("regime", "unknown")
    except Exception as e:
        log.debug(f"[pipeline] regime fetch failed: {e}")
        sig.setdefault("regime", "unknown")

    # 2. CALIBRATION
    raw_prob = sig.get("probability")
    sig["raw_probability"] = raw_prob
    try:
        from app.domain.signal.calibration import calibrate_probability
        calibrated = calibrate_probability(float(raw_prob)) if raw_prob is not None else raw_prob
    except Exception as e:
        log.debug(f"[pipeline] calibration skipped: {e}")
        calibrated = raw_prob

    # 3. ENERGY STATE
    try:
        from app.domain.data.market import fetch_ohlcv
        from app.domain.core.energy_detector import compute_energy_state, energy_signal_modifier
        edf    = fetch_ohlcv(symbol, period="3mo")
        energy = compute_energy_state(edf)
        sig["energy_state"]  = energy.get("state")
        sig["energy_score"]  = energy.get("score")
        sig["energy_bias"]   = energy.get("direction_bias")
        sig["energy_reason"] = energy.get("reason")
        if calibrated is not None:
            e_mod      = energy_signal_modifier(energy, sig.get("direction", "HOLD"))
            calibrated = round(min(float(calibrated) * e_mod["boost"], 1.0), 4)
    except Exception as e:
        log.debug(f"[pipeline] energy detection failed: {e}")
        sig.setdefault("energy_state", "unknown")

    # 4. EV GATE + MULTIPLIER
    multiplier = 1.0
    try:
        from app.domain.core.ev_calculator import should_fire
        ev_fire, ev_info = should_fire(
            sig.get("regime", "unknown"),
            sig.get("direction", "HOLD"),
            float(calibrated or raw_prob or 0.5),
        )
        multiplier             = ev_info["multiplier"]
        sig["ev_score"]        = ev_info.get("ev")
        sig["ev_source"]       = ev_info.get("source")
        sig["ev_win_rate"]     = ev_info.get("win_rate")
        if not ev_fire and sig.get("direction") in ("BUY", "SELL"):
            sig["regime_suppressed"]         = True
            sig["regime_suppression_reason"] = ev_info.get("blocked_reason", "negative_ev")
    except Exception as e:
        log.debug(f"[pipeline] EV gate failed: {e}")
        try:
            from app.domain.regime.detector import regime_multiplier
            multiplier = regime_multiplier(sig.get("regime", "unknown"), sig.get("direction", ""))
        except Exception:
            multiplier = 1.0

    # 5. FINAL PROBABILITY
    if calibrated is not None:
        sig["regime_adjusted_probability"] = round(min(float(calibrated) * multiplier, 1.0), 4)
        sig["probability"]                 = sig["regime_adjusted_probability"]
    else:
        sig["probability"] = raw_prob

    # 6. REGIME SUPPRESSION (direction vs regime logic)
    if "regime_suppressed" not in sig:
        regime    = sig.get("regime", "unknown")
        direction = sig.get("direction", "")
        if regime in ("ranging", "bear") and direction == "BUY":
            sig["regime_suppressed"]         = True
            sig["regime_suppression_reason"] = f"{regime} regime — BUY suppressed"
        elif regime == "bull" and direction == "SELL":
            sig["regime_suppressed"]         = True
            sig["regime_suppression_reason"] = "bull regime — SELL suppressed"
        else:
            sig["regime_suppressed"] = False

    # 7. CONTEXT TEXT (instant template — LLM runs in background)
    try:
        _emap = {
            "exhausted": "market overextended — mean reversion risk elevated",
            "coiled":    "market energy compressed — breakout likely imminent",
            "releasing": "momentum active — trend confirmation in play",
        }
        _ev     = sig.get("ev_score")
        _ev_str = f"EV +{_ev:.2f}%" if _ev and _ev > 0 else (f"EV {_ev:.2f}%" if _ev else "")
        _estr   = _emap.get(sig.get("energy_state", ""), "energy state neutral")
        sig["context_text"]     = (
            f"{sig.get('symbol','')} {sig.get('direction','HOLD')} signal in "
            f"{sig.get('regime','unknown')} regime with "
            f"{sig.get('probability',0):.0%} calibrated confidence; {_estr}."
            + (f" {_ev_str} based on historical outcomes." if _ev_str else "")
        )
        sig["conflict_detected"] = False
        # Full LLM interpretation in background
        from app.domain.core.context_generator import generate_signal_context
        threading.Thread(target=generate_signal_context, args=(sig.copy(),), daemon=True).start()
    except Exception as e:
        log.debug(f"[pipeline] context generation failed: {e}")


    # 8. CONFLICT RESOLVER
    try:
        from app.domain.core.context_generator import _get_symbol_history, _detect_conflict
        history = _get_symbol_history(sig.get("symbol", ""), limit=5)
        conflict = _detect_conflict(sig, history)
        sig["conflict_detected"] = conflict["detected"]
        sig["conflict_reason"]   = conflict.get("reason", "")

        if conflict["detected"]:
            reason = conflict.get("reason", "")
            # 3 consecutive losses → suppress to HOLD
            if "3 consecutive losses" in reason:
                sig["direction"]               = "HOLD"
                sig["probability"]             = min(sig.get("probability", 0.5), 0.25)
                sig["confidence"]              = "low"
                sig["regime_suppressed"]       = True
                sig["regime_suppression_reason"] = f"Conflict resolver: {reason}"
            # Last signal opposite direction and won → downgrade
            else:
                sig["probability"] = round(sig.get("probability", 0.5) * 0.70, 4)
                sig["confidence"]  = "low"
                sig["context_text"] = (
                    f"⚠️ Conflicted signal — {reason}. "
                    + sig.get("context_text", "")
                )
    except Exception as e:
        log.debug(f"[pipeline] conflict resolver failed: {e}")
    return sig
