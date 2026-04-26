"""
api/routes/portfolio_xray.py
Phase 5 — Portfolio X-Ray endpoints.
POST /portfolio/xray      — analyze holdings
GET  /portfolio/regime-fit — quick regime alignment check
"""
import logging
from fastapi import APIRouter, Depends, HTTPException
from app.api.routes.auth import get_current_user

router = APIRouter()
log = logging.getLogger(__name__)


@router.post("/portfolio/xray")
async def portfolio_xray(
    payload: dict,
    user: dict = Depends(get_current_user),
):
    """
    Analyze a portfolio of holdings.
    Body: {
        "holdings": [
            {"symbol": "RELIANCE.NS", "value": 50000, "side": "LONG", "sector": "Energy"},
            ...
        ],
        "fetch_signals": true  (optional — fetch live signals for regime check)
    }
    """
    holdings = payload.get("holdings", [])
    if not holdings:
        raise HTTPException(status_code=400, detail="holdings list is required")

    # Validate holdings format
    for h in holdings:
        if "symbol" not in h or "value" not in h:
            raise HTTPException(
                status_code=400,
                detail="Each holding needs 'symbol' and 'value' fields"
            )

    # Get current regime
    current_regime = None
    try:
        from app.domain.regime.detector import detect_regime
        regime_data    = detect_regime("NIFTY50") or {}
        current_regime = regime_data.get("regime")
    except Exception:
        pass

    # Optionally fetch live signals for each holding
    current_signals = {}
    if payload.get("fetch_signals", False):
        from app.domain.signal.service import generate_signal
        for h in holdings[:10]:  # cap at 10 to avoid timeout
            sym = h.get("symbol", "")
            try:
                sig = generate_signal(sym)
                if sig:
                    current_signals[sym] = sig
            except Exception:
                pass

    try:
        from app.domain.portfolio.xray import xray_engine
        result = xray_engine.analyze(
            holdings=holdings,
            current_signals=current_signals,
            current_regime=current_regime,
        )
        return {"status": "ok", "user_id": user.get("id"), **result.to_dict()}
    except Exception as e:
        log.error(f"[portfolio_xray] failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/portfolio/regime-fit")
async def regime_fit(
    symbols: str,
    user: dict = Depends(get_current_user),
):
    """
    Quick regime alignment check for a comma-separated list of symbols.
    GET /api/v1/portfolio/regime-fit?symbols=RELIANCE.NS,TCS.NS,HDFCBANK.NS
    """
    symbol_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not symbol_list:
        raise HTTPException(status_code=400, detail="symbols param is required")

    current_regime = None
    try:
        from app.domain.regime.detector import detect_regime
        regime_data    = detect_regime("NIFTY50") or {}
        current_regime = regime_data.get("regime")
    except Exception:
        pass

    results = []
    for sym in symbol_list[:15]:
        try:
            from app.domain.signal.service import generate_signal
            sig = generate_signal(sym) or {}
            direction = sig.get("direction", "HOLD")
            from app.domain.portfolio.xray import REGIME_CONFLICTS
            conflict_dir = REGIME_CONFLICTS.get(current_regime or "", None)
            misaligned   = (direction == conflict_dir)
            results.append({
                "symbol":     sym,
                "direction":  direction,
                "regime":     current_regime,
                "misaligned": misaligned,
                "action":     f"Consider reducing {sym}" if misaligned else "OK",
            })
        except Exception as e:
            results.append({"symbol": sym, "error": str(e)})

    aligned   = sum(1 for r in results if not r.get("misaligned") and not r.get("error"))
    fit_score = aligned / len(results) if results else 1.0

    return {
        "regime":    current_regime,
        "fit_score": round(fit_score, 3),
        "positions": results,
    }
