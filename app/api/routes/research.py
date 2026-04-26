"""
api/routes/research.py
Perseus intelligence endpoints — returns ResearchPacket for any symbol.
"""
import logging
from fastapi import APIRouter, Depends, HTTPException
from app.api.routes.auth import get_current_user

router = APIRouter()
log = logging.getLogger(__name__)


@router.get("/research/{symbol}")
async def get_ticker_research(
    symbol: str,
    user: dict = Depends(get_current_user),
):
    """
    Full intelligence packet for a symbol.
    Returns signal, regime, news evidence, risk flags, contradictions.
    """
    import time as _time
    _t0 = _time.perf_counter()
    try:
        from app.domain.research.ticker_packet import build_ticker_packet
        packet = await build_ticker_packet(symbol.upper())
        d = packet.to_dict()
        latency_ms = (_time.perf_counter() - _t0) * 1000
        # W2.4 — persist packet
        try:
            from app.domain.research.packet_store import save_packet
            user_id = user.get("sub") or user.get("user_id") if user else None
            save_packet(d, user_id=user_id)
        except Exception as _pe:
            log.warning(f"[research] packet save failed: {_pe}")
        # W4.1 — log verification result
        try:
            from app.domain.core.verifier_log import log_verification
            uid = user.get("sub") or user.get("user_id") if user else None
            log_verification(
                endpoint="/research/{symbol}",
                symbol=symbol.upper(),
                verification=d.get("verification", {}),
                latency_ms=latency_ms,
                user_id=uid,
            )
        except Exception as _vl:
            log.debug(f"[research] verifier log failed: {_vl}")
        return d
    except Exception as e:
        log.error(f"[research] ticker_packet failed for {symbol}: {e}")
        raise HTTPException(status_code=500, detail=f"Research unavailable: {str(e)}")


@router.get("/research/{symbol}/summary")
async def get_ticker_summary(
    symbol: str,
    user: dict = Depends(get_current_user),
):
    """
    Lightweight summary only — faster, for dashboard cards.
    """
    try:
        from app.domain.research.ticker_packet import build_ticker_packet
        packet = await build_ticker_packet(symbol.upper())
        return {
            "symbol":     packet.symbol,
            "summary":    packet.summary,
            "direction":  packet.direction,
            "confidence": packet.confidence.value,
            "probability": packet.probability,
            "regime":     packet.regime,
            "risk_count": len(packet.risk_flags),
            "high_risks": [r.description for r in packet.risk_flags if r.severity == "high"],
            "contradictions": packet.contradictions,
            "timestamp":  packet.timestamp.isoformat(),
        }
    except Exception as e:
        log.error(f"[research] summary failed for {symbol}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/research/history/{symbol}")
async def get_research_history(
    symbol: str,
    limit: int = 10,
    user: dict = Depends(get_current_user),
):
    """Last N research packets for a symbol — for comparison and memory."""
    from app.domain.research.packet_store import get_history
    return {"symbol": symbol.upper(), "history": get_history(symbol, limit=min(limit, 50))}
