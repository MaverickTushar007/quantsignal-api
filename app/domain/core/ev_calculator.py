"""
Expected Value Calculator — replaces static regime multipliers.
Computes EV per regime+direction from actual signal_history outcomes.
Falls back to current multipliers if insufficient data (<10 samples).
"""
import os, logging
from functools import lru_cache
from datetime import datetime, timezone, timedelta

log = logging.getLogger(__name__)

# Fallback multipliers (current hand-tuned values)
FALLBACK_MULTIPLIERS = {
    ("bear",    "BUY"):  0.05,
    ("bear",    "SELL"): 1.6,
    ("bull",    "BUY"):  1.3,
    ("bull",    "SELL"): 0.2,
    ("ranging", "BUY"):  0.05,
    ("ranging", "SELL"): 1.5,
    ("unknown", "BUY"):  0.5,
    ("unknown", "SELL"): 0.5,
}

MIN_SAMPLES = 10       # minimum trades needed to use EV instead of fallback
EV_THRESHOLD = 0.0     # minimum EV% to allow signal through (0 = break-even)
CACHE_TTL_MINUTES = 60 # refresh EV stats every hour

_ev_cache = {"data": None, "expires_at": None}


def get_ev_stats() -> dict:
    """
    Returns EV stats per (regime, direction) from signal_history.
    Cached for 60 minutes.
    """
    global _ev_cache
    now = datetime.now(timezone.utc)

    if _ev_cache["data"] and _ev_cache["expires_at"] and now < _ev_cache["expires_at"]:
        return _ev_cache["data"]

    try:
        import psycopg2
        db_url = os.environ.get("DATABASE_URL", "")
        if not db_url:
            return {}

        con = psycopg2.connect(db_url)
        cur = con.cursor()
        cur.execute("""
            SELECT
                regime,
                direction,
                outcome,
                COUNT(*) as n,
                AVG(
                    CASE
                        WHEN direction='BUY'  AND exit_price > 0 AND entry_price > 0
                            THEN (exit_price - entry_price) / entry_price * 100
                        WHEN direction='SELL' AND exit_price > 0 AND entry_price > 0
                            THEN (entry_price - exit_price) / entry_price * 100
                        ELSE NULL
                    END
                ) as avg_pnl
            FROM signal_history
            WHERE outcome IS NOT NULL
              AND regime IS NOT NULL
              AND direction IN ('BUY', 'SELL')
            GROUP BY regime, direction, outcome
        """)
        rows = cur.fetchall()
        con.close()

        # Build stats dict
        stats = {}
        for regime, direction, outcome, n, avg_pnl in rows:
            key = (regime, direction)
            if key not in stats:
                stats[key] = {"wins": 0, "losses": 0, "avg_win": 0.0, "avg_loss": 0.0, "total": 0}
            if outcome == "win":
                stats[key]["wins"] = n
                stats[key]["avg_win"] = float(avg_pnl or 0)
            elif outcome == "loss":
                stats[key]["losses"] = n
                stats[key]["avg_loss"] = float(avg_pnl or 0)

        # Compute EV for each key
        import math
        ev_stats = {}
        for key, s in stats.items():
            total = s["wins"] + s["losses"]
            s["total"] = total
            # Sanitize avg values — NaN occurs when exit_price is NULL
            avg_win  = s["avg_win"]  if s["avg_win"]  and not math.isnan(s["avg_win"])  else 0.0
            avg_loss = s["avg_loss"] if s["avg_loss"] and not math.isnan(s["avg_loss"]) else 0.0
            s["avg_win"] = avg_win
            s["avg_loss"] = avg_loss
            if total >= MIN_SAMPLES and (avg_win != 0.0 or avg_loss != 0.0):
                win_rate = s["wins"] / total
                loss_rate = 1 - win_rate
                ev = (win_rate * avg_win) + (loss_rate * avg_loss)
                s["win_rate"] = round(win_rate, 3)
                s["ev"] = round(ev, 3)
                s["sufficient_data"] = True
            else:
                s["win_rate"] = None
                s["ev"] = None
                s["sufficient_data"] = False
            ev_stats[key] = s

        _ev_cache["data"] = ev_stats
        _ev_cache["expires_at"] = now + timedelta(minutes=CACHE_TTL_MINUTES)
        log.info(f"[ev_calculator] refreshed EV stats for {len(ev_stats)} regime/direction pairs")
        return ev_stats

    except Exception as e:
        log.warning(f"[ev_calculator] failed to compute EV stats: {e}")
        return {}


def compute_ev(regime: str, direction: str) -> dict:
    """
    Returns EV info for a given regime+direction.
    {"ev": float|None, "win_rate": float|None, "sufficient_data": bool,
     "multiplier": float, "source": "ev"|"fallback"}
    """
    stats = get_ev_stats()
    key = (regime, direction)
    fallback_mult = FALLBACK_MULTIPLIERS.get(key, 0.5)

    if key in stats and stats[key]["sufficient_data"]:
        ev = stats[key]["ev"]
        win_rate = stats[key]["win_rate"]
        # Convert EV to a multiplier: positive EV → boost, negative → suppress
        # Scale: EV of +2% → multiplier ~1.5, EV of -2% → multiplier ~0.1
        if ev > 0:
            mult = min(1.0 + (ev / 2.0), 2.0)   # cap at 2x
        else:
            mult = max(0.05, 1.0 + (ev / 2.0))  # floor at 0.05x
        return {
            "ev": ev,
            "win_rate": win_rate,
            "total_samples": stats[key]["total"],
            "sufficient_data": True,
            "multiplier": round(mult, 3),
            "source": "ev_calculated",
        }
    else:
        return {
            "ev": None,
            "win_rate": None,
            "total_samples": stats.get(key, {}).get("total", 0),
            "sufficient_data": False,
            "multiplier": fallback_mult,
            "source": "fallback",
        }


def should_fire(regime: str, direction: str, probability: float) -> tuple[bool, dict]:
    """
    Master gate: should this signal fire given EV + probability?
    Returns (should_fire: bool, ev_info: dict)
    """
    ev_info = compute_ev(regime, direction)
    mult = ev_info["multiplier"]
    adjusted_prob = min(probability * mult, 1.0)

    # Block if EV is clearly negative and we have enough data
    if ev_info["sufficient_data"] and ev_info["ev"] is not None and ev_info["ev"] < -1.0:
        ev_info["blocked_reason"] = f"negative_ev_{ev_info['ev']:.2f}pct"
        return False, ev_info

    ev_info["adjusted_probability"] = round(adjusted_prob, 4)
    return True, ev_info


def get_all_ev_summary() -> list:
    """Returns human-readable EV summary for all regime+direction pairs."""
    stats = get_ev_stats()
    result = []
    for (regime, direction), s in sorted(stats.items()):
        ev_info = compute_ev(regime, direction)
        result.append({
            "regime": regime,
            "direction": direction,
            "win_rate": s.get("win_rate"),
            "ev": s.get("ev"),
            "total_trades": s.get("total", 0),
            "multiplier": ev_info["multiplier"],
            "source": ev_info["source"],
        })
    return result
