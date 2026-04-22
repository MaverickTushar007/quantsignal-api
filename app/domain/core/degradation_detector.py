"""
app/domain/core/degradation_detector.py
Detects when the ML model is degrading per symbol.
Compares 30-day rolling win rate against 90-day baseline.
Flags if drop exceeds 15 percentage points.
"""
from __future__ import annotations
import logging
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

DEGRADATION_THRESHOLD = 0.15  # 15 percentage point drop triggers flag
MIN_TRADES = 10               # minimum resolved trades to compute rate


def _query_win_rate(cur, db: str, symbol: str, days: int) -> tuple[float, int]:
    """Return (win_rate, trade_count) for a symbol over last N days."""
    ph = "%s" if db == "pg" else "?"
    if db == "pg":
        cutoff_expr = f"NOW() - INTERVAL '{days} days'"
    else:
        cutoff_expr = f"datetime('now', '-{days} days')"

    cur.execute(f"""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN outcome = 'win' THEN 1 ELSE 0 END) as wins
        FROM signal_history
        WHERE symbol = {ph}
          AND outcome IN ('win', 'loss')
          AND generated_at >= {cutoff_expr}
    """, (symbol,))
    row = cur.fetchone()
    total = row[0] or 0
    wins  = row[1] or 0
    if total < MIN_TRADES:
        return 0.0, total
    return round(wins / total, 4), total


def check_symbol(symbol: str) -> dict:
    """Check degradation for a single symbol."""
    from app.infrastructure.db.signal_history import _get_conn
    con, db = _get_conn()
    try:
        cur = con.cursor()
        wr_30,  n_30  = _query_win_rate(cur, db, symbol, 30)
        wr_90,  n_90  = _query_win_rate(cur, db, symbol, 90)
    finally:
        con.close()

    if n_30 < MIN_TRADES or n_90 < MIN_TRADES:
        return {
            "symbol":       symbol,
            "degraded":     False,
            "insufficient": True,
            "wr_30d":       wr_30,
            "wr_90d":       wr_90,
            "drop":         0.0,
            "n_30d":        n_30,
            "n_90d":        n_90,
        }

    drop = wr_90 - wr_30  # positive = win rate has fallen
    degraded = drop >= DEGRADATION_THRESHOLD

    if degraded:
        log.warning(
            f"[Degradation] {symbol} degraded — 90d_wr={wr_90} 30d_wr={wr_30} "
            f"drop={drop:.3f} (threshold={DEGRADATION_THRESHOLD})"
        )
        _send_alert(symbol, wr_90, wr_30, drop)

    return {
        "symbol":       symbol,
        "degraded":     degraded,
        "insufficient": False,
        "wr_30d":       wr_30,
        "wr_90d":       wr_90,
        "drop":         round(drop, 4),
        "n_30d":        n_30,
        "n_90d":        n_90,
    }


def check_all() -> dict[str, dict]:
    """Check degradation across all symbols with enough history."""
    from app.infrastructure.db.signal_history import _get_conn
    con, db = _get_conn()
    try:
        cur = con.cursor()
        if db == "pg":
            cur.execute("""
                SELECT DISTINCT symbol FROM signal_history
                WHERE outcome IN ('win', 'loss')
                GROUP BY symbol HAVING COUNT(*) >= 10
            """)
        else:
            cur.execute("""
                SELECT DISTINCT symbol FROM signal_history
                WHERE outcome IN ('win', 'loss')
                GROUP BY symbol HAVING COUNT(*) >= 10
            """)
        symbols = [row[0] for row in cur.fetchall()]
    finally:
        con.close()

    results = {}
    for sym in symbols:
        try:
            results[sym] = check_symbol(sym)
        except Exception as e:
            log.error(f"[Degradation] {sym} check failed: {e}")
    return results


def _send_alert(symbol: str, wr_90: float, wr_30: float, drop: float) -> None:
    try:
        import os, requests
        token   = os.environ.get("TELEGRAM_BOT_TOKEN")
        chat_id = os.environ.get("TELEGRAM_ADMIN_CHAT_ID") or os.environ.get("TELEGRAM_CHAT_ID")
        if not token or not chat_id:
            return
        msg = (
            f"⚠️ Model Degradation Alert\n\n"
            f"Symbol: {symbol}\n"
            f"90-day win rate: {round(wr_90*100,1)}%\n"
            f"30-day win rate: {round(wr_30*100,1)}%\n"
            f"Drop: {round(drop*100,1)} percentage points\n"
            f"Action: Consider retraining model for {symbol}"
        )
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": msg},
            timeout=5,
        )
    except Exception as e:
        log.error(f"[Degradation] Alert failed: {e}")
