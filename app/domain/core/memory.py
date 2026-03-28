"""
Memory layer — conversation history + user memory for Perseus.
Reads/writes from Supabase. Non-blocking, always fails safely.
"""
import os, logging
log = logging.getLogger(__name__)

DEFAULT_USER = "default"  # until auth is built


def _sb():
    from supabase import create_client
    return create_client(
        os.environ["SUPABASE_URL"],
        os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_ANON_KEY", "")
    )


# ── Conversation History ───────────────────────────────────────────────────

def save_message(user_id: str, session_id: str, role: str,
                 content: str, context: dict = {}):
    """Save a chat message to conversation_history."""
    try:
        _sb().table("conversation_history").insert({
            "user_id":    user_id,
            "session_id": session_id,
            "role":       role,
            "content":    content[:4000],
            "context":    context,
        }).execute()
    except Exception as e:
        log.debug(f"[memory] save_message failed: {e}")


def get_conversation(user_id: str, session_id: str, limit: int = 20) -> list:
    """Fetch recent messages for a session."""
    try:
        res = _sb().table("conversation_history") \
            .select("role,content,created_at") \
            .eq("user_id", user_id) \
            .eq("session_id", session_id) \
            .order("created_at", desc=False) \
            .limit(limit).execute()
        return res.data or []
    except Exception as e:
        log.debug(f"[memory] get_conversation failed: {e}")
        return []


def get_recent_sessions(user_id: str, limit: int = 5) -> list:
    """Get recent session IDs for a user."""
    try:
        res = _sb().table("conversation_history") \
            .select("session_id,created_at") \
            .eq("user_id", user_id) \
            .order("created_at", desc=True) \
            .limit(limit * 10).execute()
        seen = []
        for r in (res.data or []):
            if r["session_id"] not in seen:
                seen.append(r["session_id"])
            if len(seen) >= limit:
                break
        return seen
    except Exception as e:
        log.debug(f"[memory] get_recent_sessions failed: {e}")
        return []


# ── User Memory ────────────────────────────────────────────────────────────

def set_user_memory(user_id: str, key: str, value: dict,
                    memory_type: str = "preference"):
    """Upsert a user memory entry."""
    try:
        _sb().table("user_memory").upsert({
            "user_id":     user_id,
            "memory_type": memory_type,
            "key":         key,
            "value":       value,
            "updated_at":  "now()",
        }, on_conflict="user_id,memory_type,key").execute()
    except Exception as e:
        log.debug(f"[memory] set_user_memory failed: {e}")


def get_user_memory(user_id: str, memory_type: str = None) -> dict:
    """Fetch all memory for a user, optionally filtered by type."""
    try:
        q = _sb().table("user_memory").select("memory_type,key,value") \
            .eq("user_id", user_id)
        if memory_type:
            q = q.eq("memory_type", memory_type)
        res = q.execute()
        result = {}
        for r in (res.data or []):
            k = f"{r['memory_type']}:{r['key']}"
            result[k] = r["value"]
        return result
    except Exception as e:
        log.debug(f"[memory] get_user_memory failed: {e}")
        return {}


# ── Signal Context Retrieval ───────────────────────────────────────────────

def get_signal_context(symbol: str, limit: int = 3) -> list:
    """Fetch recent signal contexts for a symbol."""
    try:
        res = _sb().table("signal_context") \
            .select("direction,context_text,ev_score,conflict_detected,conflict_reason,generated_at") \
            .eq("symbol", symbol) \
            .order("generated_at", desc=True) \
            .limit(limit).execute()
        return res.data or []
    except Exception as e:
        log.debug(f"[memory] get_signal_context failed: {e}")
        return []


# ── Perseus Context Builder ────────────────────────────────────────────────

def build_perseus_context(user_id: str, symbol: str, session_id: str) -> str:
    """
    Build a rich context string for Perseus system prompt.
    Includes: user memory, recent signal contexts, conversation summary.
    """
    parts = []

    # User memory
    mem = get_user_memory(user_id)
    if mem:
        parts.append("USER MEMORY:")
        for k, v in list(mem.items())[:5]:
            parts.append(f"  {k}: {v}")

    # Recent signal context for this symbol
    contexts = get_signal_context(symbol, limit=2)
    if contexts:
        parts.append(f"\nRECENT {symbol} SIGNAL CONTEXT:")
        for c in contexts:
            parts.append(f"  [{c.get('generated_at','')[:10]}] {c.get('direction','')}:")
            parts.append(f"  {c.get('context_text','')}")
            if c.get("conflict_detected"):
                parts.append(f"  ⚠️ CONFLICT: {c.get('conflict_reason','')}")

    # Recent conversation summary (last 3 exchanges)
    history = get_conversation(user_id, session_id, limit=6)
    if history:
        parts.append("\nRECENT CONVERSATION:")
        for msg in history[-4:]:
            role = msg["role"].upper()
            content = msg["content"][:200]
            parts.append(f"  {role}: {content}")

    return "\n".join(parts) if parts else ""
