"""
billing/middleware.py
FastAPI dependency — injects user tier + enforces rate limits.
Usage:
    @router.get("/signals")
    def get_signals(gate: dict = Depends(signal_gate)):
        ...
"""
import os
import logging
from fastapi import Header, HTTPException, Depends
from typing import Optional

log = logging.getLogger(__name__)


def get_user_tier(user_id: str) -> str:
    """Fetch tier from Supabase user_subscriptions table."""
    if not user_id or user_id in ("default", "public"):
        return "free"
    try:
        from supabase import create_client
        sb  = create_client(
            os.environ.get("SUPABASE_URL", ""),
            os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_ANON_KEY", "")
        )
        res = sb.table("user_subscriptions") \
            .select("tier,status") \
            .eq("user_id", user_id) \
            .limit(1).execute()
        if res.data:
            row = res.data[0]
            if row.get("status") == "active":
                return row.get("tier", "free")
    except Exception as e:
        log.debug(f"[middleware] tier fetch failed: {e}")
    return "free"


def user_context(x_user_id: Optional[str] = Header(None)) -> dict:
    """Base dependency — returns user_id + tier."""
    user_id = x_user_id or "anonymous"
    tier    = get_user_tier(user_id)
    return {"user_id": user_id, "tier": tier}


def signal_gate(ctx: dict = Depends(user_context)) -> dict:
    """Dependency for signal endpoints — enforces daily signal limit."""
    from app.domain.billing.usage import check_limit
    allowed, used, limit = check_limit(ctx["user_id"], "signals", ctx["tier"])
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail={
                "error":   "Signal limit reached",
                "used":    used,
                "limit":   limit,
                "tier":    ctx["tier"],
                "upgrade": "https://quantsignal.app/pricing",
            }
        )
    from app.domain.billing.usage import increment
    from app.domain.billing.plans import token_cost
    increment(ctx["user_id"], "signals", tokens=token_cost("signal"))
    return ctx


def perseus_gate(ctx: dict = Depends(user_context)) -> dict:
    """Dependency for Perseus chat — enforces daily message limit."""
    from app.domain.billing.usage import check_limit
    allowed, used, limit = check_limit(ctx["user_id"], "perseus", ctx["tier"])
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail={
                "error":   "Perseus message limit reached",
                "used":    used,
                "limit":   limit,
                "tier":    ctx["tier"],
                "upgrade": "https://quantsignal.app/pricing",
            }
        )
    from app.domain.billing.usage import increment
    from app.domain.billing.plans import token_cost
    increment(ctx["user_id"], "perseus", tokens=token_cost("perseus_chat"))
    return ctx


def feature_gate(feature: str):
    """Factory — returns a dependency that checks if tier has a feature."""
    def _gate(ctx: dict = Depends(user_context)) -> dict:
        from app.domain.billing.plans import can_access
        if not can_access(ctx["tier"], feature):
            raise HTTPException(
                status_code=403,
                detail={
                    "error":   f"'{feature}' requires a paid plan",
                    "tier":    ctx["tier"],
                    "upgrade": "https://quantsignal.app/pricing",
                }
            )
        return ctx
    return _gate
