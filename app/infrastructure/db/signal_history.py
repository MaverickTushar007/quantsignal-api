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
        # Dedup: skip if same symbol+direction already open OR saved in last 24h
        dedup_q = (
            "SELECT COUNT(*) FROM signal_history WHERE symbol=%s AND direction=%s AND (outcome='open' OR generated_at::timestamptz > NOW() - INTERVAL '24 hours')"
            if db == "pg" else
            "SELECT COUNT(*) FROM signal_history WHERE symbol=? AND direction=? AND (outcome='open' OR generated_at > datetime('now','-24 hours'))"
        )
        cur.execute(dedup_q, (signal["symbol"], signal["direction"]))
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
                int(str(signal.get("confluence_score","0")).split("/")[0]) if signal.get("confluence_score") else None,
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
            SELECT id, symbol, direction, entry_price, take_profit, stop_loss,
                   generated_at, regime, confluence_score
            FROM signal_history
            WHERE outcome = 'open' AND direction IN ('BUY', 'SELL')
        """)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        con.close()

def update_outcome(signal_id: int, outcome: str, exit_price: float, extra: dict = None):
    con, db = _get_conn()
    extra = extra or {}
    try:
        cur = con.cursor()
        ph = "%s" if db == "pg" else "?"
        sets = ["outcome = " + ph, "exit_price = " + ph, "evaluated_at = " + ph]
        vals = [outcome, exit_price, datetime.utcnow().isoformat()]
        allowed = ["pnl_pct", "hold_time_hours", "max_favorable_excursion",
                   "max_adverse_excursion", "failure_reason", "failure_category",
                   "market_regime_at_entry"]
        for k in allowed:
            if k in extra and extra[k] is not None:
                sets.append(f"{k} = {ph}")
                vals.append(extra[k])
        vals.append(signal_id)
        q = f"UPDATE signal_history SET {', '.join(sets)} WHERE id = {ph}"
        cur.execute(q, vals)
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

        # Sharpe + expectancy
        sharpe = None
        expectancy = None
        avg_win = None
        avg_loss = None
        try:
            cur.execute("""
                SELECT outcome, entry_price, exit_price FROM signal_history
                WHERE outcome IN ('win', 'loss') AND exit_price IS NOT NULL AND entry_price > 0
            """)
            trades = cur.fetchall()
            if len(trades) >= 30:
                import math
                pnls, win_pnls, loss_pnls = [], [], []
                for outcome, entry, exit_p in trades:
                    pct = (exit_p - entry) / entry * 100
                    pnls.append(pct)
                    (win_pnls if outcome == "win" else loss_pnls).append(abs(pct))
                mean_pnl = sum(pnls) / len(pnls)
                std_pnl = (sum((x - mean_pnl)**2 for x in pnls) / len(pnls)) ** 0.5
                sharpe = round((mean_pnl / std_pnl) * math.sqrt(252), 3) if std_pnl > 0 else None
                avg_win = round(sum(win_pnls) / len(win_pnls), 3) if win_pnls else None
                avg_loss = round(sum(loss_pnls) / len(loss_pnls), 3) if loss_pnls else None
                if avg_win and avg_loss and evaluated:
                    wr = wins / evaluated
                    expectancy = round(wr * avg_win - (1 - wr) * avg_loss, 3)
        except Exception as _e:
            pass

        return {
            "win_rate": round(wins / evaluated, 3) if evaluated else None,
            "wins": wins,
            "losses": losses,
            "open": total - evaluated,
            "total_signals": total,
            "recent_trades": recent,
            "sharpe_ratio": sharpe,
            "expectancy_pct": expectancy,
            "avg_win_pct": avg_win,
            "avg_loss_pct": avg_loss,
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

def get_monte_carlo_significance(symbol: str = None, n_shuffles: int = 500) -> dict:
    """
    Monte Carlo significance test on win rate.
    Shuffles the win/loss sequence N times and checks what fraction
    of shuffles produce a win rate >= the actual rate.
    p_value < 0.10 = edge is likely real.
    p_value >= 0.10 = could be luck, flag as unverified.
    """
    import random
    con, db = _get_conn()
    try:
        cur = con.cursor()
        if symbol:
            cur.execute(
                "SELECT outcome FROM signal_history WHERE symbol=? AND outcome IN ('win','loss') ORDER BY evaluated_at ASC"
                if db != "pg" else
                "SELECT outcome FROM signal_history WHERE symbol=%s AND outcome IN ('win','loss') ORDER BY evaluated_at ASC",
                (symbol,)
            )
        else:
            cur.execute(
                "SELECT outcome FROM signal_history WHERE outcome IN ('win','loss') ORDER BY evaluated_at ASC"
            )
        outcomes = [row[0] for row in cur.fetchall()]
    finally:
        con.close()

    n = len(outcomes)
    if n < 30:
        return {
            "symbol": symbol,
            "n_trades": n,
            "win_rate": None,
            "p_value": None,
            "verified": False,
            "reason": f"insufficient_data ({n} < 30 required)"
        }

    wins = outcomes.count("win")
    actual_wr = wins / n

    # Shuffle and count how many beat actual win rate
    beats = 0
    seq = outcomes.copy()
    for _ in range(n_shuffles):
        random.shuffle(seq)
        shuffled_wr = seq.count("win") / n
        if shuffled_wr >= actual_wr:
            beats += 1

    p_value = round(beats / n_shuffles, 4)
    verified = p_value < 0.10

    return {
        "symbol": symbol,
        "n_trades": n,
        "win_rate": round(actual_wr, 4),
        "p_value": p_value,
        "verified": verified,
        "reason": "edge_confirmed" if verified else "possibly_luck"
    }
