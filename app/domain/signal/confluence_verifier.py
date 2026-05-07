"""
confluence_verifier.py
Central integrity checks for confluence across NEWS, MACRO, LIQUIDITY, TECHNICAL, etc.
"""
from __future__ import annotations
from collections import Counter
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
import logging

log = logging.getLogger(__name__)

NEWS_MAX_LAG_SEC   = 6 * 60 * 60
MACRO_MAX_LAG_SEC  = 24 * 60 * 60
LIQUID_MAX_LAG_SEC = 60 * 60

def _parse_iso(ts):
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None

def _direction_from_confluence(confs):
    score = sum(1 if (c.get("signal") or "").upper() in ("BULL","BULLISH") else -1 if (c.get("signal") or "").upper() in ("BEAR","BEARISH") else 0 for c in confs)
    return "BULL" if score > 0 else "BEAR" if score < 0 else "NEUTRAL"

def verify_confluence(cache):
    total = len(cache)
    if total == 0:
        return {"agent":"ConfluenceVerifier","run_at":datetime.now(timezone.utc).isoformat(),"total_signals":0,"coverage":{},"stale":{},"direction_conflicts":{}}

    coverage_counts, stale_counts, conflict_examples = Counter(), Counter(), []

    for sym, sig in cache.items():
        generated_at = _parse_iso(sig.get("generated_at"))
        direction    = sig.get("direction", "HOLD")
        confs        = sig.get("confluence") or []
        news         = sig.get("news") or []

        domains = {(c.get("source") or "TECHNICAL").upper() for c in confs}
        if not domains and confs:
            domains.add("TECHNICAL")
        for src in domains:
            coverage_counts[src] += 1
        if news:
            coverage_counts["NEWS"] += 1

        def check_lag(src, ts, max_lag):
            if not generated_at:
                return
            dt = _parse_iso(ts)
            if dt and (generated_at - dt).total_seconds() > max_lag:
                stale_counts[src] += 1

        for c in confs:
            src = (c.get("source") or "TECHNICAL").upper()
            check_lag(src, c.get("timestamp"), MACRO_MAX_LAG_SEC if src=="MACRO" else LIQUID_MAX_LAG_SEC if src=="LIQUIDITY" else NEWS_MAX_LAG_SEC)
        for n in news:
            check_lag("NEWS", n.get("timestamp"), NEWS_MAX_LAG_SEC)

        conf_dir = _direction_from_confluence(confs)
        exp_dir  = "BULL" if direction=="BUY" else "BEAR" if direction=="SELL" else "NEUTRAL"
        if exp_dir != "NEUTRAL" and conf_dir != "NEUTRAL" and exp_dir != conf_dir:
            conflict_examples.append({"symbol":sym,"signal_direction":direction,"confluence_dir":conf_dir,"generated_at":sig.get("generated_at")})

    run_at = datetime.now(timezone.utc).isoformat()
    result = {
        "agent": "ConfluenceVerifier",
        "run_at": run_at,
        "total_signals": total,
        "coverage": {src: {"present":c,"pct":round(c/total,3)} for src,c in sorted(coverage_counts.items())},
        "stale":    {src: {"stale":c,"pct":round(c/max(coverage_counts.get(src,1),1),3)} for src,c in sorted(stale_counts.items())},
        "direction_conflicts": {"count":len(conflict_examples),"pct":round(len(conflict_examples)/total,3),"examples":conflict_examples[:20]},
    }
    _store(result)
    return result

def _store(result):
    try:
        import os
        from supabase import create_client
        url = os.environ.get("SUPABASE_URL","")
        key = os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_ANON_KEY","")
        if url and key:
            create_client(url,key).table("agent_runs").upsert({"agent":"ConfluenceVerifier","run_at":result["run_at"],"findings":result}).execute()
    except Exception as e:
        log.debug(f"[ConfluenceVerifier] store failed: {e}")
