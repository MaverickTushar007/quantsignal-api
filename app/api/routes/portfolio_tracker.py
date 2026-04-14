"""
api/routes/portfolio_tracker.py
Per-user position tracking — add positions, track P&L vs Guardian signals.
GET  /portfolio              → current positions
POST /portfolio/position     → add/update position
DELETE /portfolio/position/{symbol} → close position
GET  /portfolio/performance  → P&L summary vs signal outcomes
"""
import logging
import os
from datetime import datetime, timezone
from fastapi import APIRouter, Header, Body
from typing import Optional

router = APIRouter()
log = logging.getLogger(__name__)


def _sb():
    from supabase import create_client
    return create_client(
        os.environ.get("SUPABASE_URL", ""),
        os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_ANON_KEY", "")
    )


@router.get("/portfolio")
def get_portfolio(x_user_id: Optional[str] = Header(None)):
    """Get all open positions for user."""
    user_id = x_user_id or "default"
    try:
        sb  = _sb()
        res = sb.table("user_positions") \
            .select("*").eq("user_id", user_id) \
            .eq("status", "open") \
            .order("entered_at", desc=True).execute()
        positions = res.data or []

        # Enrich with current price
        enriched = []
        for pos in positions:
            enriched.append(_enrich_position(pos))

        total_pnl = sum(p.get("unrealized_pnl_pct", 0) for p in enriched)
        return {
            "user_id":    user_id,
            "positions":  enriched,
            "count":      len(enriched),
            "total_pnl":  round(total_pnl, 3),
        }
    except Exception as e:
        return {"error": str(e)}


@router.post("/portfolio/position")
def add_position(
    x_user_id: Optional[str] = Header(None),
    body: dict = Body(...)
):
    """Add or update a position."""
    user_id = x_user_id or "default"
    symbol  = body.get("symbol", "").upper()
    if not symbol:
        return {"error": "symbol required"}

    try:
        sb = _sb()
        sb.table("user_positions").upsert({
            "user_id":    user_id,
            "symbol":     symbol,
            "direction":  body.get("direction", "BUY"),
            "entry_price": body.get("entry_price"),
            "quantity":   body.get("quantity", 1),
            "stop_loss":  body.get("stop_loss"),
            "take_profit": body.get("take_profit"),
            "notes":      body.get("notes", ""),
            "status":     "open",
            "entered_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
        return {"status": "ok", "symbol": symbol, "user_id": user_id}
    except Exception as e:
        return {"error": str(e)}


@router.delete("/portfolio/position/{symbol}")
def close_position(
    symbol: str,
    x_user_id: Optional[str] = Header(None),
    exit_price: Optional[float] = None
):
    """Close a position and record outcome."""
    user_id = x_user_id or "default"
    try:
        sb  = _sb()
        res = sb.table("user_positions") \
            .select("*").eq("user_id", user_id) \
            .eq("symbol", symbol.upper()).eq("status", "open").execute()

        if not res.data:
            return {"error": "No open position found"}

        pos = res.data[0]

        # Calculate P&L
        entry = pos.get("entry_price")
        pnl   = None
        if entry and exit_price:
            pnl = round((exit_price - entry) / entry * 100, 3)
            if pos.get("direction") == "SELL":
                pnl = -pnl

        sb.table("user_positions").update({
            "status":     "closed",
            "exit_price": exit_price,
            "pnl_pct":    pnl,
            "closed_at":  datetime.now(timezone.utc).isoformat(),
        }).eq("user_id", user_id).eq("symbol", symbol.upper()).execute()

        return {"status": "closed", "symbol": symbol, "pnl_pct": pnl}
    except Exception as e:
        return {"error": str(e)}


@router.get("/portfolio/performance")
def get_performance(x_user_id: Optional[str] = Header(None)):
    """P&L summary for closed positions."""
    user_id = x_user_id or "default"
    try:
        sb  = _sb()
        res = sb.table("user_positions") \
            .select("symbol,direction,pnl_pct,entered_at,closed_at") \
            .eq("user_id", user_id).eq("status", "closed") \
            .order("closed_at", desc=True).limit(50).execute()
        trades = res.data or []

        if not trades:
            return {"message": "No closed trades yet", "user_id": user_id}

        pnls   = [t["pnl_pct"] for t in trades if t.get("pnl_pct") is not None]
        wins   = sum(1 for p in pnls if p > 0)
        total  = len(pnls)
        avg    = round(sum(pnls) / total, 3) if total else 0

        return {
            "user_id":    user_id,
            "total_trades": total,
            "wins":       wins,
            "losses":     total - wins,
            "win_rate":   f"{wins/total:.0%}" if total else "N/A",
            "avg_pnl":    f"{avg:+.2f}%",
            "best_trade": f"{max(pnls):+.2f}%" if pnls else "N/A",
            "worst_trade": f"{min(pnls):+.2f}%" if pnls else "N/A",
            "recent_trades": trades[:10],
        }
    except Exception as e:
        return {"error": str(e)}


def _enrich_position(pos: dict) -> dict:
    """Add current price and unrealized P&L to position."""
    try:
        import yfinance as yf
        sym    = pos.get("symbol", "")
        ticker = yf.Ticker(sym)
        hist   = ticker.history(period="1d", interval="5m")
        if not hist.empty:
            current = float(hist["Close"].iloc[-1])
            entry   = pos.get("entry_price")
            if entry:
                pnl = (current - entry) / entry * 100
                if pos.get("direction") == "SELL":
                    pnl = -pnl
                pos["current_price"]      = round(current, 4)
                pos["unrealized_pnl_pct"] = round(pnl, 3)
    except Exception:
        pass
    return pos
