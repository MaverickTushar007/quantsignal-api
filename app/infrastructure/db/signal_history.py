"""
Signal history — Postgres (Railway) with SQLite fallback for local dev.
"""
import os
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

def _get_conn():
    DATABASE_URL = os.environ.get("DATABASE_URL")
    if DATABASE_URL:
        import psycopg2
        return psycopg2.connect(DATABASE_URL), "pg"
    else:
        import sqlite3
        from pathlib import Path
        Path("data").mkdir(exist_ok=True)
        con = sqlite3.connect("data/signal_history.db")
        con.row_factory = sqlite3.Row
        return con, "sqlite"

def init_db():
    con, db = _get_conn()
    try:
        cur = con.cursor()
        if db == "pg":
            cur.execute("""
                CREATE TABLE IF NOT EXISTS signal_history (
                    id            SERIAL PRIMARY KEY,
                    symbol        TEXT NOT NULL,
                    direction     TEXT NOT NULL,
                    entry_price   FLOAT NOT NULL,
                    take_profit   FLOAT NOT NULL,
                    stop_loss     FLOAT NOT NULL,
                    horizon_hours INT DEFAULT 24,
                    outcome       TEXT DEFAULT 'open',
                    exit_price    FLOAT,
                    generated_at  TEXT NOT NULL,
                    evaluated_at  TEXT,
                    probability      FLOAT,
                    raw_probability  FLOAT,
                    confluence_score INT,
                    mtf_score        INT,
                    regime           TEXT,
                    regime_multiplier FLOAT
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_symbol ON signal_history(symbol)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_outcome ON signal_history(outcome)")
        else:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS signal_history (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol        TEXT NOT NULL,
                    direction     TEXT NOT NULL,
                    entry_price   REAL NOT NULL,
                    take_profit   REAL NOT NULL,
                    stop_loss     REAL NOT NULL,
                    horizon_hours INTEGER DEFAULT 24,
                    outcome       TEXT DEFAULT 'open',
                    exit_price    REAL,
                    generated_at  TEXT NOT NULL,
                    evaluated_at  TEXT,
                    probability      REAL,
                    raw_probability  REAL,
                    confluence_score INTEGER,
                    mtf_score        INTEGER,
                    regime           TEXT,
                    regime_multiplier REAL
                )
            """)
        con.commit()
        logger.info(f"[signal_history] DB initialized ({db})")
    finally:
        con.close()

def is_open(symbol: str) -> bool:
    """Returns True if the market for this symbol is currently open."""
    from datetime import datetime
    import pytz

    now_utc = datetime.now(pytz.utc)
    sym = symbol.upper()

    # Crypto — always open
    if any(sym.endswith(x) for x in ("-USD", "-USDT", "BTC", "ETH")):
        return True

    # Indian market (NSE/BSE) — Mon-Fri 03:45-10:00 UTC (09:15-15:30 IST)
    if sym.endswith(".NS") or sym.endswith(".BO"):
        if now_utc.weekday() >= 5:
            return False
        market_open  = now_utc.replace(hour=3, minute=45, second=0, microsecond=0)
        market_close = now_utc.replace(hour=10, minute=0,  second=0, microsecond=0)
        return market_open <= now_utc <= market_close

    # US market — Mon-Fri 13:30-20:00 UTC (09:30-16:00 ET)
    if now_utc.weekday() >= 5:
        return False
    market_open  = now_utc.replace(hour=13, minute=30, second=0, microsecond=0)
    market_close = now_utc.replace(hour=20, minute=0,  second=0, microsecond=0)
    return market_open <= now_utc <= market_close


def market_status(symbol: str) -> dict:
    """Returns market open/closed status with human-readable label."""
    open_ = is_open(symbol)
    return {
        "is_open": open_,
        "label": "LIVE" if open_ else "MARKET CLOSED",
        "note": None if open_ else "Signal based on last available close price",
    }

def save_signal(signal: dict):
    con, db = _get_conn()
    try:
        cur = con.cursor()
        # Dedup: skip if identical signal for same symbol+direction in last 4 hours
        dedup_q = (
            "SELECT COUNT(*) FROM signal_history WHERE symbol=%s AND direction=%s AND ABS(entry_price-%s)<0.01 AND generated_at > NOW() - INTERVAL '4 hours'"
            if db == "pg" else
            "SELECT COUNT(*) FROM signal_history WHERE symbol=? AND direction=? AND ABS(entry_price-?)<0.01 AND generated_at > datetime('now','-4 hours')"
        )
        cur.execute(dedup_q, (signal["symbol"], signal["direction"], signal["current_price"]))
        if cur.fetchone()[0] > 0:
            logger.info(f"[signal_history] dedup skip {signal['symbol']} {signal['direction']}")
            return
        cur.execute(
            """INSERT INTO signal_history
              (symbol, direction, entry_price, take_profit, stop_loss, generated_at, probability, raw_probability, confluence_score, mtf_score, regime, regime_multiplier, outcome)
              VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'open')""" if db == "pg" else
            """INSERT INTO signal_history
              (symbol, direction, entry_price, take_profit, stop_loss, generated_at, probability, raw_probability, confluence_score, mtf_score, regime, regime_multiplier, outcome)
              VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open')""",
            (
                signal["symbol"],
                signal["direction"],
                signal["current_price"],
                signal["take_profit"],
                signal["stop_loss"],
                signal.get("generated_at", datetime.utcnow().isoformat()),
                signal.get("probability"),
                signal.get("raw_probability"),
                signal.get("confluence_score"),
                signal.get("mtf_score"),
                signal.get("regime"),
                signal.get("regime_multiplier"),
            )
        )
        con.commit()
    except Exception as e:
        logger.error(f"[signal_history] save failed: {e}")
    finally:
        con.close()

def get_open_signals() -> list[dict]:
    con, db = _get_conn()
    try:
        cur = con.cursor()
        cur.execute("""
            SELECT id, symbol, direction, entry_price, take_profit, stop_loss, generated_at
            FROM signal_history
            WHERE outcome = 'open' AND direction IN ('BUY', 'SELL')
        """)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        con.close()

def update_outcome(signal_id: int, outcome: str, exit_price: float):
    con, db = _get_conn()
    try:
        cur = con.cursor()
        cur.execute(
            "UPDATE signal_history SET outcome = %s, exit_price = %s, evaluated_at = %s WHERE id = %s" if db == "pg"
            else "UPDATE signal_history SET outcome = ?, exit_price = ?, evaluated_at = ? WHERE id = ?",
            (outcome, exit_price, datetime.utcnow().isoformat(), signal_id)
        )
        con.commit()
    finally:
        con.close()

def get_performance() -> dict:
    con, db = _get_conn()
    try:
        cur = con.cursor()
        cur.execute("""
            SELECT outcome, COUNT(*) as count FROM signal_history
            WHERE outcome IN ('win', 'loss') GROUP BY outcome
        """)
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, row)) for row in cur.fetchall()]

        cur.execute("SELECT COUNT(*) FROM signal_history")
        total = cur.fetchone()[0]

        cur.execute("""
            SELECT symbol, direction, entry_price, exit_price, outcome, generated_at
            FROM signal_history WHERE outcome IN ('win', 'loss')
            ORDER BY evaluated_at DESC LIMIT 10
        """)
        cols = [d[0] for d in cur.description]
        recent = [dict(zip(cols, row)) for row in cur.fetchall()]

        counts = {r["outcome"]: r["count"] for r in rows}
        wins = counts.get("win", 0)
        losses = counts.get("loss", 0)
        evaluated = wins + losses

        return {
            "win_rate": round(wins / evaluated, 3) if evaluated else None,
            "wins": wins,
            "losses": losses,
            "open": total - evaluated,
            "total_signals": total,
            "recent_trades": recent,
        }
    finally:
        con.close()

def get_evaluated_signals() -> list[dict]:
    con, db = _get_conn()
    try:
        cur = con.cursor()
        cur.execute("""
            SELECT symbol, direction, entry_price, exit_price, outcome, generated_at, evaluated_at, probability, confluence_score, mtf_score
            FROM signal_history
            WHERE outcome IN ('win', 'loss') AND exit_price IS NOT NULL
            ORDER BY evaluated_at ASC
        """)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        con.close()

def get_recent_signals(symbol: str, limit: int = 5) -> list[dict]:
    """Fetch recent live signals for a symbol — used by Perseus stream."""
    con, db = _get_conn()
    try:
        cur = con.cursor()
        cur.execute(
            "SELECT direction, probability, outcome, generated_at, entry_price, take_profit, stop_loss FROM signal_history WHERE symbol = %s ORDER BY generated_at DESC LIMIT %s" if db == "pg"
            else "SELECT direction, probability, outcome, generated_at, entry_price, take_profit, stop_loss FROM signal_history WHERE symbol = ? ORDER BY generated_at DESC LIMIT ?",
            (symbol, limit)
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception as e:
        logger.error(f"[get_recent_signals] {e}")
        return []
    finally:
        con.close()
