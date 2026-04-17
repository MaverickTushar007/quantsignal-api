"""
billing/plans.py
Single source of truth for all tier definitions and feature gates.
"""

PLANS = {
    "free": {
        "name":              "Free",
        "price_inr":         0,
        "signals_per_day":   10,
        "perseus_per_day":   5,
        "perseus_max_input_tokens": 300,
        "alerts":            False,
        "guardian":          False,
        "portfolio":         False,
        "api_access":        False,
        "all_agents":        False,
    },
    "pro": {
        "name":              "Pro",
        "price_inr":         999,
        "ls_variant_id":     "",   # fill after LS product setup
        "signals_per_day":   9999,
        "perseus_per_day":   9999,
        "perseus_max_input_tokens": 9999,
        "alerts":            True,
        "guardian":          True,
        "portfolio":         True,
        "api_access":        False,
        "all_agents":        True,
    },
    "institutional": {
        "name":              "Institutional",
        "price_inr":         2999,
        "ls_variant_id":     "",   # fill after LS product setup
        "signals_per_day":   9999,
        "perseus_per_day":   9999,
        "alerts":            True,
        "guardian":          True,
        "portfolio":         True,
        "api_access":        True,
        "all_agents":        True,
    },
}


def get_plan(tier: str) -> dict:
    return PLANS.get(tier, PLANS["free"])


def can_access(tier: str, feature: str) -> bool:
    """Check if a tier has access to a feature."""
    return bool(get_plan(tier).get(feature, False))


def signals_limit(tier: str) -> int:
    return get_plan(tier)["signals_per_day"]


def perseus_limit(tier: str) -> int:
    return get_plan(tier)["perseus_per_day"]
