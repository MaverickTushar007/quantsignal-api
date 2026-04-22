"""
api/auth.py
JWT auth via Supabase — supports ECC P-256 (JWKS) and legacy HS256.
"""
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.core.config import settings

security = HTTPBearer(auto_error=False)

SUPABASE_URL = getattr(settings, "supabase_url", None)

def _decode_supabase_jwt(token: str) -> dict | None:
    """Decode Supabase JWT — tries JWKS (ECC) first, falls back to HS256."""
    try:
        import jwt
        import json
        import urllib.request

        # Try JWKS (current Supabase default — ECC P-256)
        if SUPABASE_URL:
            jwks_url = f"{SUPABASE_URL}/auth/v1/.well-known/jwks.json"
            try:
                with urllib.request.urlopen(jwks_url, timeout=3) as r:
                    jwks = json.loads(r.read())
                from jwt.algorithms import ECAlgorithm
                # Get kid from token header
                header = jwt.get_unverified_header(token)
                kid = header.get("kid")
                key_data = None
                for k in jwks.get("keys", []):
                    if k.get("kid") == kid or not kid:
                        key_data = k
                        break
                if key_data:
                    public_key = ECAlgorithm.from_jwk(json.dumps(key_data))
                    payload = jwt.decode(
                        token,
                        public_key,
                        algorithms=["ES256"],
                        options={"verify_aud": False},
                    )
                    return payload
            except Exception:
                pass

        # Fallback: legacy HS256 shared secret
        secret = getattr(settings, "SUPABASE_JWT_SECRET", None)
        if secret:
            payload = jwt.decode(
                token,
                secret,
                algorithms=["HS256"],
                options={"verify_aud": False},
            )
            return payload

    except Exception:
        pass
    return None


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> dict:
    if not credentials or not credentials.credentials:
        return {"id": "anon", "email": None, "tier": "free"}

    token = credentials.credentials
    payload = _decode_supabase_jwt(token)

    if not payload:
        return {"id": "anon", "email": None, "tier": "free"}

    user_id = payload.get("sub", "unknown")
    email = payload.get("email", "")

    # Fetch tier from Supabase profiles table
    try:
        from app.infrastructure.db.supabase_client import get_supabase
        sb = get_supabase()
        row = sb.table("profiles").select("tier").eq("id", user_id).single().execute()
        tier = row.data.get("tier", "free") if row.data else "free"
    except Exception:
        tier = "free"

    return {"id": user_id, "email": email, "tier": tier}


async def require_pro(user: dict = Depends(get_current_user)) -> dict:
    if user.get("tier") not in ("pro", "institutional"):
        raise HTTPException(status_code=403, detail="Pro subscription required.")
    return user
