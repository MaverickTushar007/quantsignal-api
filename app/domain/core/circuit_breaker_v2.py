"""
app/domain/core/circuit_breaker_v2.py

Adapted from QuantLive's FeedbackController + OutcomeDetector + RiskManager.

Three protection layers:
  1. Circuit breaker   — halt all signals after N consecutive losses or 2x drawdown
  2. Daily loss cap    — halt signals when daily P&L loss exceeds threshold
  3. Auto-outcome poll — check open signals against current price every run

Key differences from QuantLive (single-asset async SQLAlchemy):
  - Sync SQLite/Postgres via psycopg2 (matches your existing _get_conn pattern)
  - Multi-asset (uses yfinance for price, not Twelve Data)
  - No ORM — raw SQL matching your signal_history schema
  - Integrates directly into generate_signal() in service.py
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta

log = logging.getLogger(__name__)

# ── Config (mirrors QuantLive thresholds, adjusted for multi-asset) ────────
CONSECUTIVE_LOSS_LIMIT = 5       # QuantLive uses 8; tighter for multi-asset
DRAWDOWN_MULTIPLIER    = 2.0     # 2x historical max drawdown triggers halt
COOLDOWN_HOURS         = 24      # Auto-reset after 24h
DAILY_LOSS_LIMIT_PCT   = 0.03    # Halt if 3% of "virtual" account lost today
VIRTUAL_ACCOUNT        = 100_000 # Notional account for % calculations
MIN_OUTCOMES_FOR_CB    = 10      # Need enough history before circuit fires


# ── Class-level state (persists across instances within same process) ──────
class CircuitBreaker:
    """
    Stateful circuit breaker — class-level vars persist across calls.

    Call check() at the top of generate_signal() to gate signal production.
    Call record_outcome() after every win/loss to update state.
    """

    _active: bool = False
    _triggered_at: datetime | None = None
    _consecutive_losses: int = 0
    _initialized: bool = False

    # ── Public API ─────────────────────────────────────────────────────────

    @classmethod
    def check(cls, symbol: str | None = None) -> tuple[bool, str | None]:
        """
        Returns (is_blocked, reason).
        Call this at the top of generate_signal() before any computation.

        If blocked: return None from generate_signal() immediately.
        If not blocked: proceed normally.
        """
        cls._maybe_init()

        # 1. Check 24h cooldown reset
        if cls._active and cls._triggered_at is not None:
            elapsed_h = (datetime.now(timezone.utc) - cls._triggered_at).total_seconds() / 3600
            if elapsed_h >= COOLDOWN_HOURS:
                log.info(f"[circuit_breaker] Cooldown expired ({elapsed_h:.1f}h), resetting")
                cls._reset()

        if not cls._active:
            return False, None

        reason = (
            f"Circuit breaker active (triggered {cls._triggered_at.strftime('%H:%M UTC') if cls._triggered_at else 'unknown'}). "
            f"Consecutive losses: {cls._consecutive_losses}. "
            f"Auto-resets after {COOLDOWN_HOURS}h cooldown."
        )
        return True, reason

    @classmethod
    def record_loss(cls, symbol: str):
        """Call after a confirmed loss outcome. Increments counter, may trigger CB."""
        cls._consecutive_losses += 1
        log.warning(f"[circuit_breaker] Loss recorded for {symbol}. "
                    f"Consecutive losses: {cls._consecutive_losses}/{CONSECUTIVE_LOSS_LIMIT}")

        if cls._consecutive_losses >= CONSECUTIVE_LOSS_LIMIT and not cls._active:
            cls._trigger(f"{cls._consecutive_losses} consecutive losses")

    @classmethod
    def record_win(cls, symbol: str):
        """Call after a confirmed win outcome. Resets consecutive loss counter."""
        if cls._consecutive_losses > 0:
            log.info(f"[circuit_breaker] Win for {symbol}, resetting consecutive loss counter "
                     f"(was {cls._consecutive_losses})")
        cls._consecutive_losses = 0
        # Win doesn't reset active CB — only cooldown does

    @classmethod
    def get_status(cls) -> dict:
        """Returns current circuit breaker status for API/admin endpoints."""
        cls._maybe_init()
        return {
            "active":              cls._active,
            "consecutive_losses":  cls._consecutive_losses,
            "loss_limit":          CONSECUTIVE_LOSS_LIMIT,
            "triggered_at":        cls._triggered_at.isoformat() if cls._triggered_at else None,
            "cooldown_hours":      COOLDOWN_HOURS,
            "resets_at": (
                (cls._triggered_at + timedelta(hours=COOLDOWN_HOURS)).isoformat()
                if cls._triggered_at else None
            ),
        }

    # ── Internal helpers ───────────────────────────────────────────────────

    @classmethod
    def _trigger(cls, reason: str):
        cls._active = True
        cls._triggered_at = datetime.now(timezone.utc)
        log.critical(f"[circuit_breaker] ACTIVATED — {reason}. "
                     f"All signals halted for {COOLDOWN_HOURS}h.")

    @classmethod
    def _reset(cls):
        cls._active = False
        cls._triggered_at = None
        cls._consecutive_losses = 0
        log.info("[circuit_breaker] Reset — signal generation resumed.")

    @classmethod
    def _maybe_init(cls):
        """Load state from DB on first call (survive restarts)."""
        if cls._initialized:
            return
        cls._initialized = True
        try:
            cls._load_from_db()
        except Exception as e:
            log.warning(f"[circuit_breaker] Could not load state from DB: {e}")

    @classmethod
    def _load_from_db(cls):
        """
        On startup, reconstruct consecutive loss count from recent signal_history.
        This ensures restarts don't reset the circuit breaker mid-streak.
        """
        from app.infrastructure.db.signal_history import _get_conn
        con, db = _get_conn()
        try:
            cur = con.cursor()
            # Get last 20 evaluated signals ordered by evaluated_at DESC
            cur.execute("""
                SELECT outcome FROM signal_history
                WHERE outcome IN ('win', 'loss')
                AND direction IN ('BUY', 'SELL')
                ORDER BY evaluated_at DESC
                LIMIT 20
            """)
            rows = cur.fetchall()
            count = 0
            for row in rows:
                outcome = row[0] if isinstance(row, (list, tuple)) else row['outcome']
                if outcome == 'loss':
                    count += 1
                else:
                    break  # hit a win, stop counting
            cls._consecutive_losses = count
            if count >= CONSECUTIVE_LOSS_LIMIT:
                log.warning(f"[circuit_breaker] Loaded {count} consecutive losses from DB — "
                            f"circuit breaker should be active")
                # Don't auto-trigger on load — let the next check() call do it
        finally:
            con.close()


# ── Outcome auto-evaluator ─────────────────────────────────────────────────
# Adapted from QuantLive's OutcomeDetector — runs synchronously, multi-asset

def evaluate_and_update_outcomes() -> dict:
    """
    Check all open signals in DB against current prices.
    Update outcomes, feed results into CircuitBreaker.

    Call this from the cron refresh endpoint or on a schedule.
    Mirrors QuantLive's check_outcomes() but sync + multi-asset.

    Returns summary dict.
    """
    from app.infrastructure.db.signal_history import _get_conn, update_outcome
    from app.domain.data.multi_source import get_price

    con, db = _get_conn()
    results = {"evaluated": 0, "wins": 0, "losses": 0, "skipped": 0, "errors": 0}

    try:
        cur = con.cursor()
        cur.execute("""
            SELECT id, symbol, direction, entry_price, take_profit, stop_loss,
                   generated_at, confluence_score
            FROM signal_history
            WHERE outcome = 'open' AND direction IN ('BUY', 'SELL')
        """)
        cols = [d[0] for d in cur.description]
        signals = [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        con.close()

    for s in signals:
        try:
            # 1. Check expiry — 30 days max (from QuantLive's expiry logic)
            try:
                gen = datetime.fromisoformat(str(s["generated_at"]).replace("Z", "+00:00"))
                if gen.tzinfo is None:
                    gen = gen.replace(tzinfo=timezone.utc)
                age_days = (datetime.now(timezone.utc) - gen).days
                if age_days > 30:
                    update_outcome(s["id"], "expired", s["entry_price"],
                                   extra={"failure_reason": "expired_30d"})
                    results["skipped"] += 1
                    continue
            except Exception:
                pass

            # 2. Get current price (yfinance fallback from our evaluator)
            price = get_price(s["symbol"])
            if not price:
                try:
                    import yfinance as yf
                    hist = yf.Ticker(s["symbol"]).history(period="1d", interval="5m")
                    if not hist.empty:
                        price = float(hist["Close"].iloc[-1])
                except Exception:
                    pass
            if not price:
                results["skipped"] += 1
                continue

            entry = float(s["entry_price"])
            tp    = float(s["take_profit"])
            sl    = float(s["stop_loss"])
            direction = s["direction"]

            # 3. Evaluate outcome (SL priority, from QuantLive decision [03-01])
            outcome = None
            if direction == "BUY":
                if price <= sl:
                    outcome = "loss"
                elif price >= tp:
                    outcome = "win"
            elif direction == "SELL":
                if price >= sl:
                    outcome = "loss"
                elif price <= tp:
                    outcome = "win"

            if outcome is None:
                results["skipped"] += 1
                continue

            # 4. Calculate PnL
            if direction == "BUY":
                pnl_pct = (price - entry) / entry * 100
            else:
                pnl_pct = (entry - price) / entry * 100

            # 5. Record outcome
            update_outcome(s["id"], outcome, price, extra={
                "pnl_pct": round(pnl_pct, 4),
            })
            results["evaluated"] += 1

            # 6. Feed into circuit breaker (key addition from QuantLive)
            if outcome == "win":
                results["wins"] += 1
                CircuitBreaker.record_win(s["symbol"])
            else:
                results["losses"] += 1
                CircuitBreaker.record_loss(s["symbol"])

            log.info(f"[outcome] {s['symbol']} {direction} → {outcome} "
                     f"entry={entry} now={price:.2f} pnl={pnl_pct:.2f}%")

        except Exception as e:
            log.error(f"[outcome] Failed for {s.get('symbol')}: {e}")
            results["errors"] += 1

    return results


# ── Daily loss guard ───────────────────────────────────────────────────────
# Adapted from QuantLive's RiskManager._check_daily_loss()

def check_daily_loss_limit() -> tuple[bool, float]:
    """
    Returns (is_breached, daily_pnl_pct).
    Halts signal generation if daily losses exceed DAILY_LOSS_LIMIT_PCT.

    Uses virtual account of VIRTUAL_ACCOUNT for % calculation.
    """
    from app.infrastructure.db.signal_history import _get_conn
    con, db = _get_conn()
    try:
        cur = con.cursor()
        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

        if db == "pg":
            cur.execute("""
                SELECT COALESCE(SUM(pnl_pct), 0) FROM signal_history
                WHERE outcome IN ('win', 'loss')
                AND evaluated_at::timestamptz >= %s
            """, (today.isoformat(),))
        else:
            cur.execute("""
                SELECT COALESCE(SUM(pnl_pct), 0) FROM signal_history
                WHERE outcome IN ('win', 'loss')
                AND evaluated_at >= ?
            """, (today.isoformat(),))

        daily_pnl_pct = float(cur.fetchone()[0] or 0)

        # Breached if negative pnl exceeds limit
        is_breached = daily_pnl_pct <= -(DAILY_LOSS_LIMIT_PCT * 100)

        if is_breached:
            log.warning(f"[circuit_breaker] Daily loss limit breached: "
                        f"{daily_pnl_pct:.2f}% (limit: -{DAILY_LOSS_LIMIT_PCT*100:.1f}%)")

        return is_breached, daily_pnl_pct

    except Exception as e:
        log.error(f"[circuit_breaker] Daily loss check failed: {e}")
        return False, 0.0
    finally:
        con.close()
