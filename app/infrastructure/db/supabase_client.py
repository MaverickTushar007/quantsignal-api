"""
Supabase client — singleton for DB queries.
Uses service key for server-side operations (bypasses RLS).
"""
from app.core.config import settings

_client = None

def get_supabase():
    global _client
    if _client is None:
        from supabase import create_client
        url = settings.supabase_url
        key = settings.supabase_service_key or settings.supabase_key
        if not url or not key:
            raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set")
        _client = create_client(url, key)
    return _client
