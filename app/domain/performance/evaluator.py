import logging
import yfinance as yf
from app.infrastructure.db.signal_history import get_open_signals, update_outcome

logger = logging.getLogger(__name__)

def _get_price(symbol: str) -> float | None:
    try:
        data = yf.download(symbol, period="1d", interval="5m", progress=False, auto_adjust=True)
        if not data.empty:
            price = float(data["Close"].iloc[-1])
            logger.info(f"[evaluator] {symbol} = {price}")
            return price
        logger.warning(f"[evaluator] {symbol} empty data")
    except Exception as e:
        logger.error(f"[evaluator] {symbol} failed: {e}")
    return None

def evaluate_open_signals() -> dict:
    signals = get_open_signals()
    results = {"evaluated": 0, "wins": 0, "losses": 0, "skipped": 0}

    for s in signals:
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
