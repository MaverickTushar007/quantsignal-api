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
                    probability   FLOAT,
                    confluence_score INT,
                    mtf_score        INT
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
                    probability   REAL,
                    confluence_score INTEGER,
                    mtf_score        INTEGER
                )
            """)
        con.commit()
        logger.info(f"[signal_history] DB initialized ({db})")
    finally:
        con.close()

def is_open(symbol: str) -> bool:
    con, db = _get_conn()
    try:
        cur = con.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM signal_history WHERE symbol = %s AND outcome = 'open'" if db == "pg"
            else "SELECT COUNT(*) FROM signal_history WHERE symbol = ? AND outcome = 'open'",
            (symbol,)
        )
        return cur.fetchone()[0] > 0
    finally:
        con.close()

def save_signal(signal: dict):
    con, db = _get_conn()
    try:
        cur = con.cursor()
        cur.execute(
            """INSERT INTO signal_history
              (symbol, direction, entry_price, take_profit, stop_loss, generated_at, probability, confluence_score, mtf_score)
              VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""" if db == "pg" else
            """INSERT INTO signal_history
              (symbol, direction, entry_price, take_profit, stop_loss, generated_at, probability, confluence_score, mtf_score)
              VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                signal["symbol"],
                signal["direction"],
                signal["current_price"],
                signal["take_profit"],
                signal["stop_loss"],
                signal.get("generated_at", datetime.utcnow().isoformat()),
                signal.get("probability"),
                signal.get("confluence_score"),
                signal.get("mtf_score"),
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
