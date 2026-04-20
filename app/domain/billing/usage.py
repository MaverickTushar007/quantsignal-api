"""
billing/usage.py
Per-user daily usage tracking via Supabase.
Tracks signal count, perseus count, and token spend.
Resets at midnight UTC.
"""
import os
import logging
from datetime import datetime, timezone, timedelta

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
    return {"user_id": user_id, "date": today, "signals": 0, "perseus": 0, "tokens": 0}


def increment(user_id: str, kind: str, tokens: int = 0) -> dict:
    """
    Increment usage counter for 'signals' or 'perseus', and add token spend.
    Returns updated usage row.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        sb      = _sb()
        current = get_usage(user_id)
        new_val = current.get(kind, 0) + 1
        new_tokens = current.get("tokens", 0) + tokens
        sb.table("user_usage").upsert({
            "user_id": user_id,
            "date":    today,
            kind:      new_val,
            "tokens":  new_tokens,
        }).execute()
        current[kind]    = new_val
        current["tokens"] = new_tokens
        return current
    except Exception as e:
        log.debug(f"[usage] increment failed: {e}")
        return {"user_id": user_id, "date": today, kind: 0, "tokens": 0}


def check_limit(user_id: str, kind: str, tier: str) -> tuple[bool, int, int]:
    """
    Returns (allowed, used, limit).
    Checks both the request count limit AND the token limit.
    allowed=True means user can proceed.
    """
    from app.domain.billing.plans import signals_limit, perseus_limit, tokens_limit, token_cost

    limit = signals_limit(tier) if kind == "signals" else perseus_limit(tier)
    usage = get_usage(user_id)
    used  = usage.get(kind, 0)

    # Block on request count
    if used >= limit:
        return False, used, limit

    # Also block on token budget for free tier
    if tier == "free":
        tok_limit   = tokens_limit(tier)
        tok_used    = usage.get("tokens", 0)
        cost        = token_cost(kind if kind in ("signal", "perseus_chat") else "signal")
        if tok_used + cost > tok_limit:
            log.warning(f"[usage] token limit hit: user={user_id} used={tok_used} cost={cost} limit={tok_limit}")
            return False, tok_used, tok_limit

    return True, used, limit


def get_usage_summary(user_id: str, tier: str) -> dict:
    """Full usage summary for the /usage endpoint."""
    from app.domain.billing.plans import signals_limit, perseus_limit, tokens_limit

    usage = get_usage(user_id)
    now   = datetime.now(timezone.utc)
    reset = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)

    sig_limit = signals_limit(tier)
    per_limit = perseus_limit(tier)
    tok_limit = tokens_limit(tier)

    return {
        "user_id":            user_id,
        "tier":               tier,
        "date":               now.strftime("%Y-%m-%d"),
        "reset_at":           reset.isoformat(),
        "signals": {
            "used":      usage.get("signals", 0),
            "limit":     sig_limit,
            "remaining": max(0, sig_limit - usage.get("signals", 0)),
        },
        "perseus": {
            "used":      usage.get("perseus", 0),
            "limit":     per_limit,
            "remaining": max(0, per_limit - usage.get("perseus", 0)),
        },
        "tokens": {
            "used":      usage.get("tokens", 0),
            "limit":     tok_limit,
            "remaining": max(0, tok_limit - usage.get("tokens", 0)),
        },
    }
