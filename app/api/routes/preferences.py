"""
api/routes/preferences.py
User preferences — watchlist, risk tolerance, alert thresholds, channels.
Uses existing Perseus memory system (Supabase) as backing store.
"""
import logging
from fastapi import APIRouter, Body, Header
from typing import Optional

router = APIRouter()
log = logging.getLogger(__name__)

DEFAULT_PREFS = {
    "watchlist":         [],
    "risk_tolerance":    "medium",       # low / medium / high
    "alert_threshold":   0.50,           # min probability to receive alerts
    "alert_channels":    ["push"],       # push / telegram / email
    "timezone":          "Asia/Kolkata",
    "briefing_enabled":  True,
    "ev_minimum":        0.0,            # min EV% to show signal
    "suppress_hold":     True,           # hide HOLD signals from dashboard
}

def _get_user_id(x_user_id: Optional[str]) -> str:
    return x_user_id or "default"

def _sb():
    import os
    from supabase import create_client
    return create_client(
        os.environ.get("SUPABASE_URL", ""),
        os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_ANON_KEY", "")
    )

def _load_prefs(user_id: str) -> dict:
    try:
        res = _sb().table("user_preferences").select("*") \
            .eq("user_id", user_id).limit(1).execute()
        if res.data:
            stored = res.data[0].get("preferences", {})
            return {**DEFAULT_PREFS, **stored}
    except Exception as e:
        log.debug(f"[prefs] load failed: {e}")
    return DEFAULT_PREFS.copy()

def _save_prefs(user_id: str, prefs: dict) -> bool:
    try:
        sb = _sb()
        existing = sb.table("user_preferences").select("user_id") \
            .eq("user_id", user_id).limit(1).execute()
        if existing.data:
            sb.table("user_preferences").update({"preferences": prefs}) \
                .eq("user_id", user_id).execute()
        else:
            sb.table("user_preferences").insert({
                "user_id": user_id,
                "preferences": prefs,
            }).execute()
        return True
    except Exception as e:
        log.debug(f"[prefs] save failed: {e}")
        return False


@router.get("/preferences")
def get_preferences(x_user_id: Optional[str] = Header(None)):
    user_id = _get_user_id(x_user_id)
    prefs   = _load_prefs(user_id)
    return {"user_id": user_id, "preferences": prefs}


@router.put("/preferences")
def update_preferences(
    updates: dict = Body(...),
    x_user_id: Optional[str] = Header(None),
):
    user_id = _get_user_id(x_user_id)
    prefs   = _load_prefs(user_id)
    # Only update valid keys
    valid_keys = set(DEFAULT_PREFS.keys())
    for k, v in updates.items():
        if k in valid_keys:
            prefs[k] = v
    _save_prefs(user_id, prefs)
    return {"user_id": user_id, "preferences": prefs, "updated": list(updates.keys())}


@router.post("/preferences/watchlist/add")
def add_to_watchlist(
    symbol: str = Body(..., embed=True),
    x_user_id: Optional[str] = Header(None),
):
    user_id  = _get_user_id(x_user_id)
    prefs    = _load_prefs(user_id)
    watchlist = prefs.get("watchlist", [])
    sym = symbol.upper()
    if sym not in watchlist:
        watchlist.append(sym)
        prefs["watchlist"] = watchlist
        _save_prefs(user_id, prefs)
    return {"watchlist": watchlist}


@router.post("/preferences/watchlist/remove")
def remove_from_watchlist(
    symbol: str = Body(..., embed=True),
    x_user_id: Optional[str] = Header(None),
):
    user_id   = _get_user_id(x_user_id)
    prefs     = _load_prefs(user_id)
    watchlist = [s for s in prefs.get("watchlist", []) if s != symbol.upper()]
    prefs["watchlist"] = watchlist
    _save_prefs(user_id, prefs)
    return {"watchlist": watchlist}


@router.get("/preferences/watchlist/signals")
def watchlist_signals(x_user_id: Optional[str] = Header(None)):
    """Return enriched signals for user's watchlist symbols only."""
    user_id   = _get_user_id(x_user_id)
    prefs     = _load_prefs(user_id)
    watchlist = prefs.get("watchlist", [])
    if not watchlist:
        return {"signals": [], "watchlist": []}
    from app.domain.signal.service import generate_signal
    from app.domain.signal.pipeline import enrich_signal
    signals = []
    for sym in watchlist:
        try:
            sig = generate_signal(sym, include_reasoning=False)
            if sig:
                sig = enrich_signal(sig, sym)
                signals.append(sig)
        except Exception as e:
            log.debug(f"[prefs] watchlist signal failed for {sym}: {e}")
    return {"signals": signals, "watchlist": watchlist}
