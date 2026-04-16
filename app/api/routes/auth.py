"""
api/auth.py
JWT auth via Supabase.
get_current_user — validates token, returns user dict.
require_pro — gates pro-only endpoints.
"""
import os
import logging
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.core.config import settings

log = logging.getLogger(__name__)
security = HTTPBearer(auto_error=False)

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> dict:
    """
    Validates Supabase JWT and returns user dict with real tier.
    Falls back to anonymous free user if no token provided.
    """
    if not credentials:
        return {"id": "anonymous", "email": "", "tier": "free"}

    token = credentials.credentials
    try:
        from supabase import create_client
        sb = create_client(
            os.environ.get("SUPABASE_URL", ""),
            os.environ.get("SUPABASE_SERVICE_KEY", "")
        )
        # Validate JWT with Supabase — this throws if token is invalid/expired
        user_resp = sb.auth.get_user(token)
        user = user_resp.user
        if not user:
            raise HTTPException(status_code=401, detail="Invalid token")

        user_id = user.id
        email = user.email or ""

        # Fetch tier from user_subscriptions (written by Razorpay webhook)
        tier = "free"
        try:
            res = sb.table("user_subscriptions")                 .select("tier,status")                 .eq("user_id", user_id)                 .eq("status", "active")                 .limit(1).execute()
            if res.data:
                tier = res.data[0].get("tier", "free")
        except Exception as e:
            log.warning(f"[auth] tier fetch failed for {user_id}: {e}")

        return {"id": user_id, "email": email, "tier": tier}

    except HTTPException:
        raise
    except Exception as e:
        log.warning(f"[auth] JWT validation failed: {e}")
        raise HTTPException(status_code=401, detail="Invalid or expired token")


async def require_pro(user: dict = Depends(get_current_user)) -> dict:
    if user.get("tier") not in ("pro", "institutional"):
        raise HTTPException(
            status_code=403,
            detail="Pro subscription required."
        )
    return user
