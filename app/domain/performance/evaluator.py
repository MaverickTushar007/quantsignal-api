import logging
from app.infrastructure.db.signal_history import get_open_signals, update_outcome
from app.infrastructure.cache.cache import get_cached
from app.core.config import BASE_DIR
import json

logger = logging.getLogger(__name__)

def _get_price(symbol: str) -> float | None:
    try:
        # Use cached signal price — already fetched by signal pipeline
        sig = get_cached(f"signal:{symbol}")
        if sig and sig.get("current_price"):
            return float(sig["current_price"])
        # Fallback to signals_cache.json
        cache_path = BASE_DIR / "data/signals_cache.json"
        if cache_path.exists():
            cache = json.loads(cache_path.read_text())
            sig = cache.get(symbol)
            if sig and sig.get("current_price"):
                return float(sig["current_price"])
    except Exception as e:
        logger.error(f"[evaluator] price fetch failed for {symbol}: {e}")
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
