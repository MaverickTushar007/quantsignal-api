"""
billing/usage.py
Per-user daily usage tracking via Supabase.
Increments counters, checks limits. Resets at midnight UTC.
"""
import os
import logging
from datetime import datetime, timezone

log = logging.getLogger(__name__)


def _sb():
    from supabase import create_client
    return create_client(
        os.environ.get("SUPABASE_URL", ""),
        os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_ANON_KEY", "")
    )


def get_usage(user_id: str) -> dict:
    """Get today's usage for a user."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        sb  = _sb()
        res = sb.table("user_usage") \
            .select("*") \
            .eq("user_id", user_id) \
            .eq("date", today) \
            .limit(1).execute()
        if res.data:
            return res.data[0]
    except Exception as e:
        log.debug(f"[usage] get failed: {e}")
    return {"user_id": user_id, "date": today, "signals": 0, "perseus": 0}


def increment(user_id: str, kind: str) -> dict:
    """
    Increment usage counter for 'signals' or 'perseus'.
    Returns updated usage row.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        sb      = _sb()
        current = get_usage(user_id)
        new_val = current.get(kind, 0) + 1

        sb.table("user_usage").upsert({
            "user_id": user_id,
            "date":    today,
            kind:      new_val,
        }).execute()

        current[kind] = new_val
        return current
    except Exception as e:
        log.debug(f"[usage] increment failed: {e}")
        return {"user_id": user_id, "date": today, kind: 0}


def check_limit(user_id: str, kind: str, tier: str) -> tuple[bool, int, int]:
    """
    Returns (allowed, used, limit).
    allowed=True means user can proceed.
    """
    from app.domain.billing.plans import signals_limit, perseus_limit
    limit = signals_limit(tier) if kind == "signals" else perseus_limit(tier)
    usage = get_usage(user_id)
    used  = usage.get(kind, 0)
    return used < limit, used, limit
