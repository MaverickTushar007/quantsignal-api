"""
domain/research/packet_store.py
W2.4 — Persist ResearchPackets to Supabase.
Table: research_packets
Every /research/{symbol} call saves a row.
History retrievable per symbol.
"""
import logging
import os
from typing import Optional

log = logging.getLogger(__name__)


def _client():
    from supabase import create_client
    url = os.getenv("SUPABASE_URL", "")
    key = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_ANON_KEY", "")
    return create_client(url, key)


def save_packet(packet_dict: dict, user_id: Optional[str] = None) -> bool:
    """
    Save a ResearchPacket to Supabase research_packets table.
    Returns True on success, False on failure (never raises).
    """
    try:
        sb = _client()
        v = packet_dict.get("verification", {})
        row = {
            "packet_id":        packet_dict.get("packet_id"),
            "symbol":           packet_dict.get("symbol"),
            "packet_type":      packet_dict.get("packet_type"),
            "summary":          packet_dict.get("summary"),
            "direction":        packet_dict.get("direction"),
            "probability":      packet_dict.get("probability"),
            "confidence":       packet_dict.get("confidence"),
            "regime":           packet_dict.get("regime"),
            "risk_flags":       packet_dict.get("risk_flags", []),
            "evidence":         packet_dict.get("evidence", []),
            "contradictions":   packet_dict.get("contradictions", []),
            "verifier_score":   v.get("score"),
            "citation_coverage":v.get("citation_coverage"),
            "freshness_seconds":packet_dict.get("freshness_seconds"),
            "model_used":       packet_dict.get("model_used"),
            "kelly_size":       packet_dict.get("kelly_size"),
            "stop_loss":        packet_dict.get("stop_loss"),
            "take_profit":      packet_dict.get("take_profit"),
            "user_id":          user_id,
        }
        sb.table("research_packets").insert(row).execute()
        log.info(f"[packet_store] saved packet for {packet_dict.get('symbol')}")
        return True
    except Exception as e:
        log.warning(f"[packet_store] save failed: {e}")
        return False


def get_history(symbol: str, limit: int = 10) -> list:
    """
    Fetch last N packets for a symbol ordered by created_at DESC.
    Returns empty list on failure.
    """
    try:
        sb = _client()
        res = (
            sb.table("research_packets")
            .select("packet_id,symbol,direction,confidence,probability,verifier_score,freshness_seconds,regime,created_at,summary")
            .eq("symbol", symbol.upper())
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return res.data or []
    except Exception as e:
        log.warning(f"[packet_store] history failed for {symbol}: {e}")
        return []
