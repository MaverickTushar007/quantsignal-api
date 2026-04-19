"""
Watchlist — tracks which Pro users watch which assets.
Stored in Supabase (user_watchlists table).
"""
import os
import logging

log = logging.getLogger(__name__)

SUPABASE_URL = os.getenv("SUPABASE_URL") or os.getenv("NEXT_PUBLIC_SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_KEY", "")


def get_watchers(symbol: str) -> list[dict]:
    """Get all Pro users watching a symbol with their Telegram chat IDs."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return []
    try:
        from supabase import create_client
        sb = create_client(SUPABASE_URL, SUPABASE_KEY)
        result = sb.table("user_watchlists") \
            .select("user_id, telegram_chat_id") \
            .eq("symbol", symbol) \
            .eq("alerts_enabled", True) \
            .execute()
        return result.data or []
    except Exception as e:
        log.warning(f"[watchlist] get_watchers failed: {e}")
        return []


def add_to_watchlist(user_id: str, symbol: str, telegram_chat_id: str = None) -> bool:
    """Add a symbol to a user's watchlist."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return False
    try:
        from supabase import create_client
        sb = create_client(SUPABASE_URL, SUPABASE_KEY)
        sb.table("user_watchlists").upsert({
            "user_id": user_id,
            "symbol": symbol,
            "telegram_chat_id": telegram_chat_id,
            "alerts_enabled": True,
        }).execute()
        return True
    except Exception as e:
        log.error(f"[watchlist] add failed: {e}")
        return False


def remove_from_watchlist(user_id: str, symbol: str) -> bool:
    """Remove a symbol from a user's watchlist."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return False
    try:
        from supabase import create_client
        sb = create_client(SUPABASE_URL, SUPABASE_KEY)
        sb.table("user_watchlists") \
            .delete() \
            .eq("user_id", user_id) \
            .eq("symbol", symbol) \
            .execute()
        return True
    except Exception as e:
        log.error(f"[watchlist] remove failed: {e}")
        return False
