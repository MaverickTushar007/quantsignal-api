"""
Platt scaling calibration — loads coefficients from DB and applies sigmoid transform.
Converts raw_probability → calibrated probability reflecting actual win rates.
"""
import logging
import math

logger = logging.getLogger(__name__)

_cache = {}  # module-level cache so we don't hit DB on every request


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def load_calibration_params() -> dict:
    """Load latest Platt scaling params from DB. Cached in memory."""
    if _cache.get("params"):
        return _cache["params"]
    try:
        from app.infrastructure.db.signal_history import _get_conn
        conn, db = _get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT params FROM calibration_params
            ORDER BY created_at DESC LIMIT 1
        """)
        row = cur.fetchone()
        conn.close()
        if row:
            import json as _json
            p = row[0] if isinstance(row[0], dict) else _json.loads(row[0])
            _cache["params"] = p
            return p
    except Exception as e:
        logger.warning(f"[calibration] Could not load params: {e}")
    return None


def calibrate_probability(raw_prob: float) -> float:
    """
    Apply Platt scaling: sigmoid(coef * raw_prob + intercept).
    Falls back to raw_prob if calibration params unavailable.
    """
    params = load_calibration_params()
    if not params:
        return round(raw_prob, 4)
    try:
        coef      = float(params["coef"])
        intercept = float(params["intercept"])
        cal = _sigmoid(coef * raw_prob + intercept)
        return round(cal, 4)
    except Exception as e:
        logger.warning(f"[calibration] Failed to apply: {e}")
        return round(raw_prob, 4)
