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
    try:
        from app.domain.research.ticker_packet import build_ticker_packet
        packet = await build_ticker_packet(symbol.upper())
        return packet.to_dict()
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
