"""
Error Logger — structured, non-blocking, pattern-aware.
Never crashes the pipeline. Always fails silently.
"""
import os, logging
from datetime import datetime, timezone

log = logging.getLogger(__name__)

def _sb():
    from supabase import create_client
    return create_client(
        os.environ.get("SUPABASE_URL", ""),
        os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_ANON_KEY", "")
    )

def log_error(component: str, error_type: str, symbol: str = None,
              message: str = "", context: dict = {}):
    """Log a structured error. Never raises — always silent on failure."""
    try:
        sb = _sb()
        # Check if same error pattern exists in last hour
        existing = sb.table("system_errors").select("id,pattern_count") \
            .eq("component", component) \
            .eq("error_type", error_type) \
            .eq("resolved", False) \
            .eq("symbol", symbol or "") \
            .gte("timestamp", _hour_ago()) \
            .limit(1).execute()

        if existing.data:
            # Increment pattern count instead of inserting duplicate
            row_id = existing.data[0]["id"]
            count = existing.data[0]["pattern_count"] + 1
            sb.table("system_errors").update({
                "pattern_count": count,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }).eq("id", row_id).execute()
            log.warning(f"[error_logger] {component}/{error_type} pattern×{count}: {message}")
        else:
            sb.table("system_errors").insert({
                "component": component,
                "error_type": error_type,
                "symbol": symbol,
                "message": message[:500],
                "context": context,
                "resolved": False,
                "pattern_count": 1,
            }).execute()
            log.warning(f"[error_logger] NEW {component}/{error_type} {symbol}: {message}")
    except Exception as e:
        log.debug(f"[error_logger] failed to log error: {e}")

def resolve_errors(component: str, error_type: str, symbol: str = None):
    """Mark errors as resolved after fix."""
    try:
        q = _sb().table("system_errors").update({"resolved": True}) \
            .eq("component", component).eq("error_type", error_type)
        if symbol:
            q = q.eq("symbol", symbol)
        q.execute()
    except Exception as e:
        log.debug(f"[error_logger] resolve failed: {e}")

def get_error_summary() -> dict:
    """Get unresolved error counts by component."""
    try:
        res = _sb().table("system_errors").select("component,error_type,pattern_count") \
            .eq("resolved", False).execute()
        rows = res.data or []
        by_component = {}
        for r in rows:
            c = r["component"]
            if c not in by_component:
                by_component[c] = {"count": 0, "types": []}
            by_component[c]["count"] += r.get("pattern_count", 1)
            by_component[c]["types"].append(r["error_type"])
        return {"total_errors": len(rows), "by_component": by_component}
    except Exception as e:
        return {"error": str(e)}

def _hour_ago() -> str:
    from datetime import timedelta
    return (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()


def detect_signal_patterns():
    """
    Scan signal_history for failure patterns and log them.
    Called after evaluate_open_signals() in cron.
    Detects: regime/direction combos with >60% loss rate in last 50 signals.
    """
    try:
        from app.infrastructure.db.signal_history import _get_conn
        con, db = _get_conn()
        cur = con.cursor()

        if db == "pg":
            cur.execute("""
                SELECT regime, direction,
                       COUNT(*) as total,
                       SUM(CASE WHEN outcome = 'loss' THEN 1 ELSE 0 END) as losses
                FROM signal_history
                WHERE outcome IN ('win', 'loss')
                  AND generated_at >= NOW() - INTERVAL '7 days'
                  AND regime IS NOT NULL
                GROUP BY regime, direction
                HAVING COUNT(*) >= 5
            """)
        else:
            cur.execute("""
                SELECT regime, direction,
                       COUNT(*) as total,
                       SUM(CASE WHEN outcome = 'loss' THEN 1 ELSE 0 END) as losses
                FROM signal_history
                WHERE outcome IN ('win', 'loss')
                  AND generated_at >= datetime('now', '-7 days')
                  AND regime IS NOT NULL
                GROUP BY regime, direction
                HAVING COUNT(*) >= 5
            """)

        rows = cur.fetchall()
        con.close()

        flagged = []
        for regime, direction, total, losses in rows:
            loss_rate = losses / total if total > 0 else 0
            if loss_rate > 0.60:
                log_error(
                    component="signal_pipeline",
                    error_type=f"high_loss_rate_{regime}_{direction}",
                    message=f"{regime} {direction} signals: {loss_rate:.0%} loss rate over {total} trades (last 7d)",
                    context={"regime": regime, "direction": direction,
                             "loss_rate": round(loss_rate, 3), "total": total, "losses": int(losses)}
                )
                flagged.append({"regime": regime, "direction": direction,
                                "loss_rate": round(loss_rate, 3), "total": total})
            elif loss_rate < 0.30 and total >= 5:
                # Good pattern — resolve any existing error for this combo
                resolve_errors("signal_pipeline", f"high_loss_rate_{regime}_{direction}")

        log.info(f"[pattern_detector] checked {len(rows)} regime/direction combos, flagged {len(flagged)}")
        return {"checked": len(rows), "flagged": flagged}

    except Exception as e:
        log.debug(f"[pattern_detector] failed: {e}")
        return {"error": str(e)}
