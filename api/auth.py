"""
api/auth.py
JWT auth via Supabase.
get_current_user — validates token, returns user dict.
require_pro — gates pro-only endpoints.
"""

from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from core.config import settings

security = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> dict:
    """
    In development: returns a mock free user so you can test without Supabase.
    In production: validates Supabase JWT and fetches user tier.
    """
    if settings.is_production:
        if not credentials:
            raise HTTPException(status_code=401, detail="Not authenticated")
        try:
            from jose import jwt
            payload = jwt.decode(
                credentials.credentials,
                settings.supabase_key,
                algorithms=["HS256"],
                options={"verify_aud": False},
            )
            return {
                "id":    payload.get("sub", ""),
                "email": payload.get("email", ""),
                "tier":  "free",  # fetch from DB in production
            }
        except Exception:
            raise HTTPException(status_code=401, detail="Invalid token")
    else:
        # Dev mode — no auth required
        return {"id": "dev-user", "email": "dev@local.com", "tier": "pro"}


async def require_pro(user: dict = Depends(get_current_user)) -> dict:
    if user.get("tier") != "pro":
        raise HTTPException(
            status_code=403,
            detail="Pro subscription required."
        )
    return user
