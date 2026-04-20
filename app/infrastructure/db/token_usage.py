"""
Token-level rate limiting for QuantSignal.
Free tier: 50,000 tokens/day
Pro tier:  unlimited

Token estimation:
  - Signal request:  ~800 tokens (prompt + indicators + reasoning)
  - Perseus chat:    ~2,000 tokens per turn
  - Bulk scan:       ~400 tokens per symbol
"""
import os
import logging
from datetime import date
from fastapi import HTTPException

log = logging.getLogger(__name__)

FREE_DAILY_LIMIT  = int(os.getenv("FREE_DAILY_TOKEN_LIMIT",  "50000"))
PRO_DAILY_LIMIT   = int(os.getenv("PRO_DAILY_TOKEN_LIMIT",  "999999"))

# Token cost estimates per request type
TOKEN_COSTS = {
    "signal":        800,
    "perseus_chat": 2000,
    "bulk_scan":     400,
    "reasoning":    1200,
    "default":       500,
}


def _get_conn():
    import psycopg2
    url = os.environ.get("DATABASE_URL")
    if not url:
        return None
    return psycopg2.connect(url)


def get_usage(user_id: str, for_date: date = None) -> dict:
    """Return today's token usage for a user."""
    for_date = for_date or date.today()
    con = _get_conn()
    if not con:
        return {"tokens_used": 0, "requests": 0}
    try:
        cur = con.cursor()
        cur.execute(
            "SELECT tokens_used, requests FROM token_usage WHERE user_id=%s AND date=%s",
            (user_id, for_date)
        )
        row = cur.fetchone()
        return {"tokens_used": row[0], "requests": row[1]} if row else {"tokens_used": 0, "requests": 0}
    finally:
        con.close()


def record_usage(user_id: str, tokens: int, request_type: str = "default") -> dict:
    """Add token usage for a user. Returns updated totals."""
    con = _get_conn()
    if not con:
        return {"tokens_used": 0, "requests": 0}
    try:
        cur = con.cursor()
        cur.execute("""
            INSERT INTO token_usage (user_id, date, tokens_used, requests, updated_at)
            VALUES (%s, CURRENT_DATE, %s, 1, NOW())
            ON CONFLICT (user_id, date) DO UPDATE SET
                tokens_used = token_usage.tokens_used + EXCLUDED.tokens_used,
                requests    = token_usage.requests + 1,
                updated_at  = NOW()
            RETURNING tokens_used, requests
        """, (user_id, tokens))
        row = cur.fetchone()
        con.commit()
        return {"tokens_used": row[0], "requests": row[1]}
    finally:
        con.close()


def check_and_consume(user_id: str, tier: str, request_type: str = "default") -> dict:
    """
    Check if user is within limit, then record usage.
    Raises HTTP 429 if over limit.
    Returns usage dict with remaining tokens.
    """
    limit = FREE_DAILY_LIMIT if tier == "free" else PRO_DAILY_LIMIT
    cost  = TOKEN_COSTS.get(request_type, TOKEN_COSTS["default"])

    usage = get_usage(user_id)
    current = usage["tokens_used"]

    if current + cost > limit:
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        reset_at = (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        ).isoformat()
        log.warning(f"[token_limit] {user_id} tier={tier} used={current} cost={cost} limit={limit}")
        raise HTTPException(
            status_code=429,
            detail={
                "error":        "daily_token_limit_reached",
                "tier":         tier,
                "tokens_used":  current,
                "tokens_limit": limit,
                "reset_at":     reset_at,
                "upgrade_url":  "https://quantsignal.io/upgrade",
            }
        )

    updated = record_usage(user_id, cost, request_type)
    return {
        "tokens_used":      updated["tokens_used"],
        "tokens_remaining": max(0, limit - updated["tokens_used"]),
        "tokens_limit":     limit,
        "requests_today":   updated["requests"],
    }
