"""
Signal history — SQLite store for outcome tracking.
Postgres-ready schema, easy to migrate later.
"""
import sqlite3
import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = Path("data/signal_history.db")
DB_PATH.parent.mkdir(exist_ok=True)

def _conn():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    with _conn() as con:
        con.execute("""
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
                evaluated_at  TEXT
            )
        """)
        con.execute("CREATE INDEX IF NOT EXISTS idx_symbol ON signal_history(symbol)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_outcome ON signal_history(outcome)")
    logger.info("[signal_history] DB initialized")

def save_signal(signal: dict):
    try:
        with _conn() as con:
            con.execute("""
                INSERT INTO signal_history
                  (symbol, direction, entry_price, take_profit, stop_loss, generated_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                signal["symbol"],
                signal["direction"],
                signal["current_price"],
                signal["take_profit"],
                signal["stop_loss"],
                signal.get("generated_at", datetime.utcnow().isoformat()),
            ))
    except Exception as e:
        logger.error(f"[signal_history] save failed: {e}")

def get_open_signals() -> list[dict]:
    with _conn() as con:
        rows = con.execute("""
            SELECT * FROM signal_history
            WHERE outcome = 'open'
            AND direction IN ('BUY', 'SELL')
        """).fetchall()
    return [dict(r) for r in rows]

def update_outcome(signal_id: int, outcome: str, exit_price: float):
    with _conn() as con:
        con.execute("""
            UPDATE signal_history
            SET outcome = ?, exit_price = ?, evaluated_at = ?
            WHERE id = ?
        """, (outcome, exit_price, datetime.utcnow().isoformat(), signal_id))

def get_performance() -> dict:
    with _conn() as con:
        rows = con.execute("""
            SELECT outcome, COUNT(*) as count
            FROM signal_history
            WHERE outcome IN ('win', 'loss')
            GROUP BY outcome
        """).fetchall()

        total_row = con.execute(
            "SELECT COUNT(*) as count FROM signal_history"
        ).fetchone()

        recent = con.execute("""
            SELECT symbol, direction, entry_price, exit_price, outcome, generated_at
            FROM signal_history
            WHERE outcome IN ('win', 'loss')
            ORDER BY evaluated_at DESC
            LIMIT 10
        """).fetchall()

    counts = {r["outcome"]: r["count"] for r in rows}
    wins = counts.get("win", 0)
    losses = counts.get("loss", 0)
    total_evaluated = wins + losses

    return {
        "win_rate": round(wins / total_evaluated, 3) if total_evaluated else None,
        "wins": wins,
        "losses": losses,
        "open": total_row["count"] - total_evaluated,
        "total_signals": total_row["count"],
        "recent_trades": [dict(r) for r in recent],
    }
