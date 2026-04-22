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
    V1: No token = free tier anonymous user.
    V2: Will validate Supabase JWT and fetch real user tier.
    """
    if not credentials or not credentials.credentials:
        return {"id": "anon", "email": None, "tier": "free"}

    token = credentials.credentials

    # Try Supabase JWT validation
    try:
        import jwt
        from app.core.config import settings
        supabase_jwt_secret = getattr(settings, "SUPABASE_JWT_SECRET", None)
        if supabase_jwt_secret:
            payload = jwt.decode(
                token,
                supabase_jwt_secret,
                algorithms=["HS256"],
                options={"verify_aud": False},
            )
            user_id = payload.get("sub", "unknown")
            email = payload.get("email", "")

            # Fetch tier from Supabase profiles
            try:
                from app.infrastructure.db.supabase_client import get_supabase
                sb = get_supabase()
                row = sb.table("profiles").select("tier").eq("user_id", user_id).single().execute()
                tier = row.data.get("tier", "free") if row.data else "free"
            except Exception:
                tier = "free"

            return {"id": user_id, "email": email, "tier": tier}
    except Exception:
        pass

    # Invalid or unverifiable token — treat as free
    return {"id": f"unverified-{token[:8]}", "email": None, "tier": "free"}


async def require_pro(user: dict = Depends(get_current_user)) -> dict:
    if user.get("tier") not in ("pro", "institutional"):
        raise HTTPException(
            status_code=403,
            detail="Pro subscription required."
        )
    return user
