import logging, requests
from app.infrastructure.db.signal_history import get_open_signals, update_outcome
logger = logging.getLogger(__name__)

def _get_price(symbol: str) -> float | None:
    """Fetch latest price via Yahoo Finance direct — no yfinance."""
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=5d"
        r = requests.get(url, headers=headers, timeout=10)
        result = r.json().get("chart", {}).get("result", [])
        if result:
            closes = result[0]["indicators"]["quote"][0].get("close", [])
            closes = [c for c in closes if c is not None]
            if closes:
                price = float(closes[-1])
                logger.info(f"[evaluator] {symbol} = {price}")
                return price
        logger.warning(f"[evaluator] {symbol} empty data")
    except Exception as e:
        logger.error(f"[evaluator] {symbol} failed: {e}")
    return None

def evaluate_open_signals() -> dict:
    from datetime import datetime, timezone
    signals = get_open_signals()
    results = {"evaluated": 0, "wins": 0, "losses": 0, "skipped": 0, "expired": 0}
    for s in signals:
        # Expire signals older than 7 days with no outcome
        try:
            gen = datetime.fromisoformat(s["generated_at"].replace("Z", "+00:00"))
            if gen.tzinfo is None:
                gen = gen.replace(tzinfo=timezone.utc)
            age_days = (datetime.now(timezone.utc) - gen).days
            if age_days > 7:
                update_outcome(s["id"], "expired", s["entry_price"])
                results["expired"] += 1
                logger.info(f"[evaluator] {s['symbol']} expired after {age_days}d")
                continue
        except Exception:
            pass
        # Skip signals with bad TP/SL data
        if s["take_profit"] == s["entry_price"] or s["stop_loss"] == s["entry_price"]:
            update_outcome(s["id"], "expired", s["entry_price"])
            results["expired"] += 1
            logger.info(f"[evaluator] {s['symbol']} expired — bad TP/SL data")
            continue
        price = _get_price(s["symbol"])
        if not price:
            results["skipped"] += 1
            continue
        direction = s["direction"]
        outcome = None
        if direction == "BUY":
            if price >= s["take_profit"]:
                outcome = "win"
            elif price <= s["stop_loss"]:
                outcome = "loss"
        elif direction == "SELL":
            if price <= s["take_profit"]:
                outcome = "win"
            elif price >= s["stop_loss"]:
                outcome = "loss"
        if outcome:
            update_outcome(s["id"], outcome, price)
            results["evaluated"] += 1
            results[f"{outcome}s"] += 1
            logger.info(f"[evaluator] {s['symbol']} {direction} → {outcome} @ {price}")
        else:
            results["skipped"] += 1
    return results
