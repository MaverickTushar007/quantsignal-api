"""
Signal Validator — catches bad data before it enters the pipeline.
Returns (is_valid, reason) tuple. Never raises.
"""
import logging
from app.domain.core.error_logger import log_error

log = logging.getLogger(__name__)

def validate_signal(sig: dict) -> tuple[bool, str]:
    """
    Validate a signal dict before saving or alerting.
    Returns (True, 'ok') or (False, 'reason').
    """
    symbol = sig.get("symbol", "UNKNOWN")

    # 1. Price sanity
    price = sig.get("current_price", 0)
    if not price or price <= 0:
        log_error("signal_validator", "invalid_price", symbol,
                  f"current_price={price}", {"signal": sig})
        return False, "invalid_price"

    # 2. Probability sanity
    prob = sig.get("probability", -1)
    if not (0 < prob < 1):
        log_error("signal_validator", "invalid_probability", symbol,
                  f"probability={prob}", {"signal": sig})
        return False, "invalid_probability"

    # 3. Direction sanity
    direction = sig.get("direction", "")
    if direction not in ("BUY", "SELL", "HOLD"):
        log_error("signal_validator", "invalid_direction", symbol,
                  f"direction={direction}", {"signal": sig})
        return False, "invalid_direction"

    # 4. TP/SL logic (only for BUY/SELL)
    if direction in ("BUY", "SELL"):
        tp = sig.get("take_profit", 0)
        sl = sig.get("stop_loss", 0)

        if direction == "BUY":
            if tp and tp <= price:
                log_error("signal_validator", "tp_below_entry", symbol,
                          f"BUY tp={tp} <= price={price}")
                return False, "tp_below_entry_on_buy"
            if sl and sl >= price:
                log_error("signal_validator", "sl_above_entry", symbol,
                          f"BUY sl={sl} >= price={price}")
                return False, "sl_above_entry_on_buy"

        if direction == "SELL":
            if tp and tp >= price:
                log_error("signal_validator", "tp_above_entry", symbol,
                          f"SELL tp={tp} >= price={price}")
                return False, "tp_above_entry_on_sell"
            if sl and sl <= price:
                log_error("signal_validator", "sl_below_entry", symbol,
                          f"SELL sl={sl} <= price={price}")
                return False, "sl_below_entry_on_sell"

    # 5. Suspiciously high raw confidence
    raw_prob = sig.get("raw_probability", prob)
    if raw_prob > 0.95:
        log_error("signal_validator", "suspiciously_high_confidence", symbol,
                  f"raw_probability={raw_prob} — possible data error")
        # Don't reject — just log. High conf might be real.

    # 6. Symbol format sanity
    if not symbol or len(symbol) > 20 or " " in symbol:
        log_error("signal_validator", "invalid_symbol", symbol,
                  f"symbol='{symbol}'")
        return False, "invalid_symbol"

    return True, "ok"
