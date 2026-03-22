"""
api/history.py
Signal history + equity curve endpoint.
"""
from fastapi import APIRouter
from pathlib import Path
import json

router = APIRouter()

@router.get("/history/summary", tags=["history"])
def history_summary():
    try:
        data = json.loads(Path("data/signal_history.json").read_text())
        trades = data.get("trades", [])
        equity_curve = [
            {"date": t["date"], "cumulative_pnl": t["cumulative_pnl"]}
            for t in trades
        ]
        return {
            "total_trades":       data["total_trades"],
            "win_rate":           data["win_rate"],
            "high_conf_win_rate": data["high_conf_win_rate"],
            "high_conf_trades":   data["high_conf_trades"],
            "total_pnl":          data["total_pnl"],
            "tp_hits":            data["tp_hits"],
            "sl_hits":            data["sl_hits"],
            "generated_at":       data["generated_at"],
            "equity_curve":       equity_curve,
        }
    except Exception as e:
        return {"error": str(e)}

@router.get("/history/trades", tags=["history"])
def history_trades(limit: int = 50, confidence: str = None, symbol: str = None):
    try:
        data = json.loads(Path("data/signal_history.json").read_text())
        trades = data.get("trades", [])
        if confidence:
            trades = [t for t in trades if t["confidence"].upper() == confidence.upper()]
        if symbol:
            trades = [t for t in trades if symbol.upper() in t["symbol"].upper()]
        trades = list(reversed(trades))
        return {"total": len(trades), "trades": trades[:limit]}
    except Exception as e:
        return {"error": str(e)}
