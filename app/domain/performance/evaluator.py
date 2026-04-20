import logging
from datetime import datetime, timezone
from app.infrastructure.db.signal_history import get_open_signals, update_outcome
from app.domain.data.multi_source import get_price as _get_price

logger = logging.getLogger(__name__)


def _get_ohlcv_range(symbol: str, period: str = "5d"):
    """Fetch OHLCV for MAE/MFE calculation."""
    try:
        from app.domain.data.fetcher import fetch_ohlcv
        df = fetch_ohlcv(symbol, period=period)
        return df
    except Exception:
        return None


def _classify_failure(s: dict, current_price: float, hold_hours: float) -> tuple:
    """Return (failure_reason, failure_category)."""
    entry = s["entry_price"]
    sl = s["stop_loss"]
    confluence = s.get("confluence_score") or 0
    try:
        confluence = int(str(confluence).split("/")[0])
    except Exception:
        confluence = 0

    if hold_hours < 2:
        return "stop_hit_within_2h", "early_stop"
    if confluence < 4:
        return "low_confluence_at_entry", "bad_setup"
    move_pct = abs(current_price - entry) / entry * 100
    if move_pct < 0.3:
        return "price_barely_moved", "no_follow_through"
    return "target_not_reached", "general"


def evaluate_open_signals() -> dict:
    signals = get_open_signals()
    results = {"evaluated": 0, "wins": 0, "losses": 0, "skipped": 0, "expired": 0}

    for s in signals:
        try:
            # ── Bad TP/SL check ──
            if s["take_profit"] == s["entry_price"] or s["stop_loss"] == s["entry_price"]:
                update_outcome(s["id"], "expired", s["entry_price"],
                               extra={"failure_reason": "bad_tp_sl",
                                      "failure_category": "data_error"})
                results["expired"] += 1
                continue

            price = _get_price(s["symbol"])
            if not price:
                results["skipped"] += 1
                continue

            direction = s["direction"]
            entry = s["entry_price"]
            tp = s["take_profit"]
            sl = s["stop_loss"]

            # ── Check TP/SL FIRST, then expiry ──
            outcome = None
            if direction == "BUY":
                if price >= tp:
                    outcome = "win"
                elif price <= sl:
                    outcome = "loss"
            elif direction == "SELL":
                if price <= tp:
                    outcome = "win"
                elif price >= sl:
                    outcome = "loss"

            # ── Expiry check (only if no TP/SL hit) ──
            if outcome is None:
                try:
                    gen = datetime.fromisoformat(s["generated_at"].replace("Z", "+00:00"))
                    if gen.tzinfo is None:
                        gen = gen.replace(tzinfo=timezone.utc)
                    age_days = (datetime.now(timezone.utc) - gen).days
                    if age_days > 30:
                        update_outcome(s["id"], "expired", s["entry_price"],
                                       extra={"failure_reason": "expired_30d",
                                              "failure_category": "timeout"})
                        results["expired"] += 1
                        continue
                except Exception:
                    pass

            # ── Hold time ──
            try:
                gen = datetime.fromisoformat(s["generated_at"].replace("Z", "+00:00"))
                if gen.tzinfo is None:
                    gen = gen.replace(tzinfo=timezone.utc)
                hold_hours = (datetime.now(timezone.utc) - gen).total_seconds() / 3600
            except Exception:
                hold_hours = None

            # ── PnL % ──
            if direction == "BUY":
                pnl_pct = (price - entry) / entry * 100
            else:
                pnl_pct = (entry - price) / entry * 100

            # ── MAE / MFE from OHLCV ──
            mae, mfe = None, None
            try:
                df = _get_ohlcv_range(s["symbol"], period="5d")
                if df is not None and len(df) > 0:
                    highs = df["High"].values
                    lows = df["Low"].values
                    if direction == "BUY":
                        mfe = float((max(highs) - entry) / entry * 100)
                        mae = float((entry - min(lows)) / entry * 100)
                    else:
                        mfe = float((entry - min(lows)) / entry * 100)
                        mae = float((max(highs) - entry) / entry * 100)
            except Exception:
                pass

            if outcome:
                extra = {
                    "pnl_pct": round(pnl_pct, 4),
                    "hold_time_hours": round(hold_hours, 2) if hold_hours else None,
                    "max_favorable_excursion": round(mfe, 4) if mfe else None,
                    "max_adverse_excursion": round(mae, 4) if mae else None,
                    "market_regime_at_entry": s.get("regime"),
                }
                if outcome == "loss":
                    reason, category = _classify_failure(s, price, hold_hours or 99)
                    extra["failure_reason"] = reason
                    extra["failure_category"] = category

                update_outcome(s["id"], outcome, price, extra=extra)
                results["evaluated"] += 1
                results[f"{outcome}s"] += 1
                logger.info(f"[evaluator] {s['symbol']} {direction} → {outcome} "
                            f"PnL={pnl_pct:.2f}% hold={hold_hours:.1f}h")
            else:
                results["skipped"] += 1

        except Exception as e:
            logger.error(f"[evaluator] Failed for {s.get('symbol')}: {e}")
            results["skipped"] += 1

    return results
