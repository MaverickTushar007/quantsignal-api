"""
signal_embeddings.py — Sprint 4 memory layer
Stores signals as 16-dim feature vectors in Supabase pgvector.
Retrieves similar past setups for Perseus context.
No external embedding API needed — we embed the ML features directly.
"""
import os
import logging
import hashlib
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

SUPABASE_URL = os.getenv("SUPABASE_URL") or os.getenv("NEXT_PUBLIC_SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_KEY", "")


def _build_feature_vector(signal: dict) -> list[float]:
    """
    Convert a signal dict into a 16-dim normalized feature vector.
    Dimensions:
      0  — probability (0-1)
      1  — confluence_bulls / 9
      2  — direction: BUY=1, SELL=0, HOLD=0.5
      3  — regime: bull=1, bear=0, neutral=0.5, unknown=0.5
      4  — model_agreement (0-1)
      5  — volume_ratio capped at 3, normalized /3
      6  — risk_reward capped at 5, normalized /5
      7  — atr / entry_price (volatility ratio)
      8  — raw_probability (0-1)
      9  — mtf_score / 8
      10 — energy: high=1, low=0, medium=0.5, unknown=0.5
      11 — conflict_detected: 1=yes, 0=no
      12 — regime_multiplier capped at 1.5, normalized
      13 — confluence_score_raw / 9
      14 — hour of day / 24 (time-of-day pattern)
      15 — day of week / 6
    """
    prob      = float(signal.get("probability") or 0.5)
    raw_prob  = float(signal.get("raw_probability") or prob)
    direction = signal.get("direction", "HOLD")
    dir_val   = 1.0 if direction == "BUY" else (0.0 if direction == "SELL" else 0.5)

    regime = str(signal.get("regime") or "unknown").lower()
    regime_val = 1.0 if "bull" in regime else (0.0 if "bear" in regime else 0.5)

    conf_str = str(signal.get("confluence_score") or "0/9")
    try:
        bulls = int(conf_str.split("/")[0])
    except Exception:
        bulls = 0

    mtf = signal.get("mtf_score") or signal.get("mtf") or 0
    if isinstance(mtf, dict):
        mtf = sum(1 for v in mtf.values() if v == direction)
    mtf_val = min(float(mtf), 8.0) / 8.0

    energy = str(signal.get("energy_state") or "unknown").lower()
    energy_val = 1.0 if "high" in energy else (0.0 if "low" in energy else 0.5)

    entry = float(signal.get("current_price") or signal.get("entry_price") or 1)
    atr   = float(signal.get("atr") or 0)
    atr_ratio = min(atr / entry, 0.1) / 0.1 if entry > 0 else 0.5

    rr  = min(float(signal.get("risk_reward") or 2.0), 5.0) / 5.0
    vol = min(float(signal.get("volume_ratio") or 1.0), 3.0) / 3.0
    ma  = float(signal.get("model_agreement") or 0)
    reg_mult = min(float(signal.get("regime_multiplier") or 1.0), 1.5) / 1.5
    conflict = 1.0 if signal.get("conflict_detected") else 0.0

    now = datetime.utcnow()
    hour_val = now.hour / 24.0
    dow_val  = now.weekday() / 6.0

    return [
        prob, bulls / 9.0, dir_val, regime_val, ma, vol, rr, atr_ratio,
        raw_prob, mtf_val, energy_val, conflict, reg_mult, bulls / 9.0,
        hour_val, dow_val,
    ]


def store_embedding(signal: dict) -> bool:
    """Store a signal embedding in Supabase. Called after every signal generation."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        logger.warning("[embeddings] Supabase not configured, skipping")
        return False
    try:
        from supabase import create_client
        sb = create_client(SUPABASE_URL, SUPABASE_KEY)

        vec = _build_feature_vector(signal)
        conf_str = str(signal.get("confluence_score") or "0/9")

        sb.table("signal_embeddings").insert({
            "symbol":          signal.get("symbol", ""),
            "direction":       signal.get("direction", "HOLD"),
            "probability":     float(signal.get("probability") or 0),
            "confluence_score": conf_str,
            "top_features":    signal.get("top_features") or [],
            "regime":          signal.get("regime") or "unknown",
            "entry_price":     float(signal.get("current_price") or signal.get("entry_price") or 0),
            "take_profit":     float(signal.get("take_profit") or 0),
            "stop_loss":       float(signal.get("stop_loss") or 0),
            "embedding":       vec,
            "generated_at":    datetime.utcnow().isoformat(),
        }).execute()
        logger.info(f"[embeddings] stored for {signal.get('symbol')}")
        return True
    except Exception as e:
        logger.error(f"[embeddings] store failed: {e}", exc_info=True)
        print(f"[embeddings] store failed: {e}", flush=True)
        return False


def find_similar(signal: dict, limit: int = 3) -> list[dict]:
    """
    Find past signals with similar feature patterns.
    Returns list of similar signals with their outcomes.
    """
    if not SUPABASE_URL or not SUPABASE_KEY:
        return []
    try:
        from supabase import create_client
        sb = create_client(SUPABASE_URL, SUPABASE_KEY)

        vec = _build_feature_vector(signal)
        vec_str = "[" + ",".join(f"{v:.6f}" for v in vec) + "]"

        # pgvector cosine similarity search via RPC
        result = sb.rpc("match_signals", {
            "query_embedding": vec_str,
            "match_symbol": signal.get("symbol", ""),
            "match_count": limit,
        }).execute()

        return result.data or []
    except Exception as e:
        logger.error(f"[embeddings] similarity search failed: {e}")
        return []


def format_similar_for_prompt(similar: list[dict]) -> str:
    """Format similar past signals for Perseus prompt injection."""
    if not similar:
        return "No similar past setups found."
    lines = []
    for s in similar:
        outcome = s.get("outcome", "open")
        outcome_label = "✓ TP hit" if outcome == "TP_HIT" else ("✗ SL hit" if outcome == "SL_HIT" else "open")
        lines.append(
            f"- {s.get('symbol')} {s.get('direction')} "
            f"{float(s.get('probability',0))*100:.0f}% conf → {outcome_label} "
            f"(confluence {s.get('confluence_score')}, regime {s.get('regime')})"
        )
    return "\n".join(lines)
