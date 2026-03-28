"""
Auto-calibration — runs weekly, updates Platt scaling params in DB.
Can be triggered via cron endpoint or run manually.
"""
import os, logging
import numpy as np

log = logging.getLogger(__name__)

def run_calibration() -> dict:
    """
    Fetch closed signals, fit Platt scaling, save to DB.
    Returns summary dict.
    """
    try:
        import psycopg2
        from sklearn.linear_model import LogisticRegression

        db_url = os.environ.get("DATABASE_URL", "")
        if not db_url:
            return {"error": "no DATABASE_URL"}

        con = psycopg2.connect(db_url)
        cur = con.cursor()

        # Fetch closed signals with raw probability
        cur.execute("""
            SELECT raw_probability, outcome
            FROM signal_history
            WHERE outcome IS NOT NULL
              AND raw_probability IS NOT NULL
              AND raw_probability > 0
        """)
        rows = cur.fetchall()

        if len(rows) < 20:
            con.close()
            return {"error": f"insufficient data: {len(rows)} samples (need 20+)"}

        X = np.array([[r[0]] for r in rows])
        y = np.array([1 if r[1] == "win" else 0 for r in rows])

        win_rate = float(y.mean())
        model = LogisticRegression(C=1.0)
        model.fit(X, y)

        coef      = float(model.coef_[0][0])
        intercept = float(model.intercept_[0])

        # Save to DB — params stored as JSON
        import json
        params_json = json.dumps({"coef": coef, "intercept": intercept})
        cur.execute("""
            INSERT INTO calibration_params (method, params, win_rate, n_samples, created_at)
            VALUES (%s, %s, %s, %s, NOW())
        """, ("platt_scaling", params_json, win_rate, len(rows)))

        con.commit()
        con.close()

        result = {
            "status": "ok",
            "n_samples": len(rows),
            "win_rate": round(win_rate, 4),
            "coef": round(coef, 4),
            "intercept": round(intercept, 4),
        }
        log.info(f"[auto_calibrate] {result}")

        # Log to error logger as info (reuse infrastructure)
        try:
            from app.domain.core.error_logger import log_error
            # Resolve any previous calibration errors
            from app.domain.core.error_logger import resolve_errors
            resolve_errors("auto_calibrate", "calibration_failed")
        except Exception:
            pass

        return result

    except Exception as e:
        log.error(f"[auto_calibrate] failed: {e}")
        try:
            from app.domain.core.error_logger import log_error
            log_error("auto_calibrate", "calibration_failed", message=str(e))
        except Exception:
            pass
        return {"error": str(e)}
