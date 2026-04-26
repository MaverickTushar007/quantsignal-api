"""
domain/data/freshness.py
W2.1 — Canonical freshness tiers and TTL map.
Every data source has a defined staleness threshold.
Crossing the threshold downgrades confidence one tier.
"""
from enum import Enum
from typing import Optional


class FreshnessTier(str, Enum):
    REALTIME  = "realtime"   # TTL: 60s   — price, spread, vol
    INTRADAY  = "intraday"   # TTL: 3600s — news, sentiment, signals
    DAILY     = "daily"      # TTL: 86400s — fundamentals, screener
    ON_DEMAND = "on_demand"  # TTL: None  — uploads, user queries


# TTL in seconds per tier
FRESHNESS_TTL: dict[FreshnessTier, Optional[int]] = {
    FreshnessTier.REALTIME:  60,
    FreshnessTier.INTRADAY:  3600,
    FreshnessTier.DAILY:     86400,
    FreshnessTier.ON_DEMAND: None,
}

# Map data source names → their tier
DATA_FRESHNESS_MAP: dict[str, FreshnessTier] = {
    "price":          FreshnessTier.REALTIME,
    "spread":         FreshnessTier.REALTIME,
    "volatility":     FreshnessTier.REALTIME,
    "signal":         FreshnessTier.INTRADAY,
    "news":           FreshnessTier.INTRADAY,
    "sentiment":      FreshnessTier.INTRADAY,
    "regime":         FreshnessTier.INTRADAY,
    "confluence":     FreshnessTier.INTRADAY,
    "fundamentals":   FreshnessTier.DAILY,
    "earnings":       FreshnessTier.DAILY,
    "screener":       FreshnessTier.DAILY,
    "research_packet":FreshnessTier.INTRADAY,
    "document":       FreshnessTier.ON_DEMAND,
    "portfolio":      FreshnessTier.ON_DEMAND,
}


def get_ttl(source: str) -> Optional[int]:
    """Return TTL in seconds for a named data source. None = no expiry."""
    tier = DATA_FRESHNESS_MAP.get(source, FreshnessTier.DAILY)
    return FRESHNESS_TTL[tier]


def get_tier(source: str) -> FreshnessTier:
    return DATA_FRESHNESS_MAP.get(source, FreshnessTier.DAILY)


def is_stale(source: str, age_seconds: int) -> bool:
    """True if data is older than its tier TTL."""
    ttl = get_ttl(source)
    if ttl is None:
        return False
    return age_seconds > ttl


def staleness_label(age_seconds: int) -> str:
    """Human-readable freshness label for UI."""
    if age_seconds < 120:
        return "live"
    if age_seconds < 3600:
        return f"{age_seconds // 60}m old"
    if age_seconds < 86400:
        return f"{age_seconds // 3600}h old"
    return f"{age_seconds // 86400}d old"


def confidence_after_staleness(confidence: str, age_seconds: int, source: str = "signal") -> str:
    """
    Downgrade confidence one tier if data is stale.
    HIGH   → MODERATE if age > intraday TTL (1h)
    MODERATE → LOW    if age > daily TTL (24h)
    LOW / INSUFFICIENT → unchanged (already degraded)
    """
    if not is_stale(source, age_seconds):
        return confidence

    tier_map = {"high": "moderate", "moderate": "low", "low": "low", "insufficient": "insufficient"}
    return tier_map.get(str(confidence).lower(), confidence)
