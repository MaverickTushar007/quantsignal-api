"""
api/auth.py
JWT auth via Supabase.
get_current_user — validates token, returns user dict.
require_pro — gates pro-only endpoints.
"""

from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.core.config import settings

security = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> dict:
    """
    V1: Currently returning a mock pro user for everyone so the app is usable.
    V2: Will validate Supabase JWT and fetch real user tier.
    """
    return {"id": "v1-public-user", "email": "public@quantsignal.com", "tier": "pro"}


async def require_pro(user: dict = Depends(get_current_user)) -> dict:
    if user.get("tier") != "pro":
        raise HTTPException(
            status_code=403,
            detail="Pro subscription required."
        )
    return user
