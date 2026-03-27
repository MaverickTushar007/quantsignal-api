import time

# symbol -> last alert timestamp
_alerted: dict[str, float] = {}
COOLDOWN_HOURS = 6

def should_alert(symbol: str) -> bool:
    last = _alerted.get(symbol, 0)
    if time.time() - last > COOLDOWN_HOURS * 3600:
        _alerted[symbol] = time.time()
        return True
    return False
