"""
Signal outcome tracker — logs signals when fired, scores them 5 days later.
Called by: cron/check-outcomes (already exists in cron.py)
"""
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

LOG_PATH = Path("data/signal_outcomes.jsonl")

def log_signal(symbol: str, direction: str, probability: float,
               entry_price: float, confidence: str):
    """Call this when a non-HOLD signal is generated."""
    if direction == "HOLD":
        return
    record = {
        "symbol": symbol,
        "direction": direction,
        "probability": round(probability, 4),
        "confidence": confidence,
        "entry_price": round(entry_price, 4),
        "fired_at": datetime.now(timezone.utc).isoformat(),
        "score_at": (datetime.now(timezone.utc) + timedelta(days=5)).date().isoformat(),
        "outcome": None,
        "exit_price": None,
        "pct_return": None,
        "correct": None,
    }
    with open(LOG_PATH, "a") as f:
        f.write(json.dumps(record) + "\n")

def score_outcomes():
    """
    Score any pending outcomes where score_at <= today.
    Fetches current price and marks correct/incorrect.
    Returns summary dict.
    """
    if not LOG_PATH.exists():
        return {"scored": 0, "pending": 0}

    import sys
    sys.path.insert(0, ".")
    from app.domain.data.market import fetch_ohlcv

    today = datetime.now(timezone.utc).date().isoformat()
    lines = LOG_PATH.read_text().strip().split("\n")
    records = [json.loads(l) for l in lines if l.strip()]

    pending = [r for r in records if r["outcome"] is None and r["score_at"] <= today]
    scored_count = 0

    for rec in pending:
        try:
            df = fetch_ohlcv(rec["symbol"])
            if df is None or len(df) < 2:
                continue
            exit_price = float(df["Close"].iloc[-1])
            pct = (exit_price - rec["entry_price"]) / rec["entry_price"] * 100
            if rec["direction"] == "SELL":
                pct = -pct
            rec["exit_price"] = round(exit_price, 4)
            rec["pct_return"] = round(pct, 3)
            rec["correct"] = pct > 0
            rec["outcome"] = "WIN" if pct > 0 else "LOSS"
            scored_count += 1
        except Exception as e:
            rec["outcome"] = f"ERROR: {e}"

    # Rewrite file with updated records
    LOG_PATH.write_text("\n".join(json.dumps(r) for r in records) + "\n")

    # Build summary
    scored = [r for r in records if r["outcome"] in ("WIN", "LOSS")]
    wins = sum(1 for r in scored if r["outcome"] == "WIN")
    summary = {
        "scored": scored_count,
        "pending": len([r for r in records if r["outcome"] is None]),
        "total_tracked": len(records),
        "all_time_wins": wins,
        "all_time_losses": len(scored) - wins,
        "live_win_rate": round(wins / len(scored) * 100, 1) if scored else None,
    }
    return summary

def get_stats() -> dict:
    """Return current win/loss stats for API endpoint."""
    if not LOG_PATH.exists():
        return {"total": 0, "wins": 0, "losses": 0, "win_rate": None, "recent": []}
    lines = LOG_PATH.read_text().strip().split("\n")
    records = [json.loads(l) for l in lines if l.strip()]
    scored = [r for r in records if r["outcome"] in ("WIN", "LOSS")]
    wins = sum(1 for r in scored if r["outcome"] == "WIN")
    recent = sorted(scored, key=lambda x: x["fired_at"], reverse=True)[:10]
    return {
        "total": len(records),
        "wins": wins,
        "losses": len(scored) - wins,
        "pending": len([r for r in records if r["outcome"] is None]),
        "win_rate": round(wins / len(scored) * 100, 1) if scored else None,
        "recent": recent,
    }
