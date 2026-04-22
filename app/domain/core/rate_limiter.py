"""
Token-level rate limiting for QuantSignal API.
Free tier: 5 signal requests per 24h per user.
Pro tier: unlimited.
Uses Redis (Upstash) with 24h TTL keys.
"""
from datetime import datetime, timezone

FREE_DAILY_LIMIT = 5


def check_rate_limit(user_id: str, tier: str) -> dict:
    """
    Returns {"allowed": True} or {"allowed": False, "retry_after": seconds, "used": n, "limit": n}
    """
    if tier == "pro" or tier == "institutional":
        return {"allowed": True, "tier": tier, "limit": None, "used": None}

    try:
        from app.infrastructure.cache.cache import _get_redis
        r = _get_redis()
        if not r:
            # Redis down — fail open, allow request
            return {"allowed": True, "tier": tier, "limit": FREE_DAILY_LIMIT, "used": 0}

        key = f"ratelimit:{user_id}:{datetime.now(timezone.utc).strftime('%Y-%m-%d')}"
        current = r.get(key)
        used = int(current) if current else 0

        if used >= FREE_DAILY_LIMIT:
            # Calculate seconds until midnight UTC
            now = datetime.now(timezone.utc)
            from datetime import timedelta
            midnight = (now + timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            retry_after = int((midnight - now).total_seconds())
            return {
                "allowed": False,
                "tier": tier,
                "used": used,
                "limit": FREE_DAILY_LIMIT,
                "retry_after": retry_after,
                "message": f"Free tier limit reached ({FREE_DAILY_LIMIT}/day). Upgrade to Pro for unlimited signals.",
            }

        # Increment counter, set 25h TTL (covers timezone edge cases)
        r.incr(key)
        r.expire(key, 90000)

        return {
            "allowed": True,
            "tier": tier,
            "used": used + 1,
            "limit": FREE_DAILY_LIMIT,
            "remaining": FREE_DAILY_LIMIT - (used + 1),
        }

    except Exception:
        # Fail open — never block on rate limiter error
        return {"allowed": True, "tier": tier, "limit": FREE_DAILY_LIMIT, "used": 0}


def get_usage(user_id: str) -> dict:
    """Get current usage for a user without incrementing."""
    try:
        from app.infrastructure.cache.cache import _get_redis
        r = _get_redis()
        if not r:
            return {"used": 0, "limit": FREE_DAILY_LIMIT}
        key = f"ratelimit:{user_id}:{datetime.now(timezone.utc).strftime('%Y-%m-%d')}"
        current = r.get(key)
        used = int(current) if current else 0
        return {"used": used, "limit": FREE_DAILY_LIMIT, "remaining": max(0, FREE_DAILY_LIMIT - used)}
    except Exception:
        return {"used": 0, "limit": FREE_DAILY_LIMIT}


def reset_user(user_id: str) -> bool:
    """Admin: reset a user's daily quota."""
    try:
        from app.infrastructure.cache.cache import _get_redis
        r = _get_redis()
        if not r:
            return False
        key = f"ratelimit:{user_id}:{datetime.now(timezone.utc).strftime('%Y-%m-%d')}"
        r.delete(key)
        return True
    except Exception:
        return False
