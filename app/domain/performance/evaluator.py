import logging
import requests
from app.infrastructure.db.signal_history import get_open_signals, update_outcome

logger = logging.getLogger(__name__)

# Internal Railway URL — avoids external routing
BASE_URL = "http://localhost:8080"

def _get_price(symbol: str) -> float | None:
    # Try internal first, then external
    for url in [f"http://localhost:8080/api/v1/signals/{symbol}",
                f"https://quantsignal-api-production.up.railway.app/api/v1/signals/{symbol}"]:
        try:
            r = requests.get(url, timeout=15)
            if r.status_code == 200:
                data = r.json()
                price = data.get("current_price")
                if price:
                    logger.info(f"[evaluator] {symbol} price: {price}")
                    return float(price)
        except Exception as e:
            logger.warning(f"[evaluator] {url} failed: {e}")
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
