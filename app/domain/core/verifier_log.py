"""
domain/core/verifier_log.py
W4.1 — Log every verification result to Supabase verifier_logs table.
Tracks pass rate, citation coverage, latency per endpoint.
Target: verifier pass rate > 80% within 4 weeks.
"""
import logging
import os
import time
from typing import Optional

log = logging.getLogger(__name__)


def _client():
    from supabase import create_client
    url = os.getenv("SUPABASE_URL", "")
    key = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_ANON_KEY", "")
    return create_client(url, key)


def log_verification(
    endpoint: str,
    symbol: Optional[str],
    verification: dict,
    latency_ms: float,
    user_id: Optional[str] = None,
) -> None:
    """
    Write one row to verifier_logs. Never raises — fire and forget.
    """
    try:
        sb = _client()
        row = {
            "endpoint":         endpoint,
            "symbol":           symbol,
            "score":            verification.get("score"),
            "passed":           verification.get("passed"),
            "citation_coverage":verification.get("citation_coverage"),
            "claims_verified":  verification.get("claims_verified"),
            "numeric_checked":  verification.get("numeric_checked"),
            "issues":           verification.get("issues", []),
            "latency_ms":       round(latency_ms, 1),
            "user_id":          user_id,
        }
        sb.table("verifier_logs").insert(row).execute()
    except Exception as e:
        log.debug(f"[verifier_log] write failed: {e}")


def get_pass_rate(endpoint: Optional[str] = None, days: int = 7) -> dict:
    """
    Compute verifier pass rate for last N days.
    Returns {pass_rate, total, passed, failed, avg_score, avg_latency_ms}.
    """
    try:
        from datetime import datetime, timedelta, timezone
        sb = _client()
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        q = sb.table("verifier_logs").select("passed,score,latency_ms").gte("created_at", since)
        if endpoint:
            q = q.eq("endpoint", endpoint)
        res = q.execute()
        rows = res.data or []
        if not rows:
            return {"pass_rate": None, "total": 0, "passed": 0, "failed": 0}
        total   = len(rows)
        passed  = sum(1 for r in rows if r.get("passed"))
        scores  = [r["score"] for r in rows if r.get("score") is not None]
        latencies = [r["latency_ms"] for r in rows if r.get("latency_ms") is not None]
        return {
            "pass_rate":      round(passed / total, 3),
            "total":          total,
            "passed":         passed,
            "failed":         total - passed,
            "avg_score":      round(sum(scores) / len(scores), 3) if scores else None,
            "avg_latency_ms": round(sum(latencies) / len(latencies), 1) if latencies else None,
            "days":           days,
            "endpoint":       endpoint,
        }
    except Exception as e:
        log.warning(f"[verifier_log] pass_rate failed: {e}")
        return {"pass_rate": None, "error": str(e)}
