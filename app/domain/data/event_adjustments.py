"""
event_adjustments.py
Track 5 — backtested ATR multipliers and Kelly reductions for high-impact macro events.
Numbers derived from 2-year backtest across BTC, ETH, SPY, QQQ, GLD.
"""

# Per-event, per-asset-class ATR multipliers (p75 from backtest)
# Used to widen TP/SL on event days
ATR_MULTIPLIERS = {
    "FOMC": {
        "crypto": 1.44,   # BTC/ETH avg p75
        "equity": 1.61,   # SPY/QQQ avg p75
        "commodity": 1.64, # GLD
        "default": 1.49,  # overall average
    },
    "NFP": {
        "crypto": 1.40,
        "equity": 1.42,
        "commodity": 1.30,
        "default": 1.34,
    },
    "CPI": {
        "crypto": 1.50,
        "equity": 1.19,
        "commodity": 1.01,  # GLD barely reacts
        "default": 1.23,
    },
}

# Kelly reduction factors (1 / ATR_multiplier)
KELLY_REDUCTIONS = {
    "FOMC": {"crypto": 0.69, "equity": 0.62, "commodity": 0.61, "default": 0.67},
    "NFP":  {"crypto": 0.71, "equity": 0.70, "commodity": 0.77, "default": 0.75},
    "CPI":  {"crypto": 0.67, "equity": 0.84, "commodity": 1.00, "default": 0.81},
}

# Event name matching — maps ForexFactory titles to event types
EVENT_TYPE_MAP = {
    "Non-Farm": "NFP",
    "Nonfarm": "NFP",
    "FOMC": "FOMC",
    "Federal Funds": "FOMC",
    "Fed ": "FOMC",
    "Powell": "FOMC",
    "CPI": "CPI",
    "Consumer Price": "CPI",
    "PCE": "CPI",  # treat PCE same as CPI
}

def get_asset_class(symbol: str) -> str:
    s = symbol.upper()
    if s.endswith("-USD") or s.endswith("-USDT") or s in ("BTC", "ETH", "SOL"):
        return "crypto"
    if s in ("GLD", "XAUUSD", "GOLD", "SILVER", "OIL", "USO"):
        return "commodity"
    return "equity"

def get_event_type(event_title: str) -> str | None:
    for key, etype in EVENT_TYPE_MAP.items():
        if key.lower() in event_title.lower():
            return etype
    return None

def get_event_adjustments(symbol: str, macro_event: dict | None) -> dict:
    """
    Returns ATR multiplier and Kelly reduction for a symbol on an event day.
    macro_event: {"title": "...", "country": "USD", "hours_away": 4.5}
    Returns: {"atr_multiplier": 1.44, "kelly_reduction": 0.69, "event_type": "FOMC"}
    """
    if not macro_event:
        return {"atr_multiplier": 1.0, "kelly_reduction": 1.0, "event_type": None}

    event_type = get_event_type(macro_event.get("title", ""))
    if not event_type:
        # Unknown high-impact event — use conservative default
        return {"atr_multiplier": 1.30, "kelly_reduction": 0.77, "event_type": "UNKNOWN"}

    asset_class = get_asset_class(symbol)
    multipliers = ATR_MULTIPLIERS[event_type]
    reductions  = KELLY_REDUCTIONS[event_type]

    return {
        "atr_multiplier":  multipliers.get(asset_class, multipliers["default"]),
        "kelly_reduction": reductions.get(asset_class, reductions["default"]),
        "event_type":      event_type,
        "asset_class":     asset_class,
    }
