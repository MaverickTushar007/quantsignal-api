from fastapi import APIRouter
from app.infrastructure.db.signal_history import get_open_signals, init_db
from app.infrastructure.db.signal_history import get_performance, init_db
from app.domain.performance.evaluator import evaluate_open_signals

router = APIRouter()
try:
    init_db()
except Exception as e:
    import logging
    logging.getLogger(__name__).error(f'[performance] init_db failed: {e}')

@router.get("/performance", tags=["quant"])
def get_performance_stats():
    return get_performance()

@router.get("/performance/debug", tags=["quant"])
def debug_signals():
    """Show raw open signals from DB for debugging."""
    return {"open_signals": get_open_signals()}

@router.post("/performance/evaluate", tags=["quant"])
def run_evaluation():
    results = evaluate_open_signals()
    return {"status": "done", **results}

from app.domain.performance.portfolio import compute_portfolio, compute_dual_portfolio
from app.infrastructure.db.signal_history import get_evaluated_signals
from fastapi import Query

@router.get("/portfolio", tags=["quant"])
def get_portfolio(
    min_prob: float = Query(0.65, description="Minimum signal probability"),
    min_confluence: int = Query(0, description="Minimum confluence score"),
    min_mtf: int = Query(0, description="Minimum MTF score"),
    compare: bool = Query(True, description="Show filtered vs unfiltered comparison"),
):
    signals = get_evaluated_signals()
    if compare:
        return compute_dual_portfolio(signals, min_prob, min_confluence, min_mtf)
    return compute_portfolio(signals)

from app.domain.performance.calibration import calibrate

@router.get("/calibration", tags=["quant"])
def get_calibration():
    signals = get_evaluated_signals()
    return calibrate(signals)


@router.get("/performance/walk-forward")
def get_walk_forward():
    """Run walk-forward validation on key symbols and return results."""
    from app.domain.ml.walk_forward import validate_all
    symbols = ["BTC-USD", "ETH-USD", "SOL-USD", "TSLA", "RELIANCE.NS", "GC=F"]
    results = validate_all(symbols)
    return {
        sym: {
            "is_win_rate":    r.is_win_rate,
            "oos_win_rate":   r.oos_win_rate,
            "wfe_ratio":      r.wfe_ratio,
            "is_trades":      r.is_trades,
            "oos_trades":     r.oos_trades,
            "is_overfitted":  r.is_overfitted,
            "insufficient":   r.insufficient_data,
            "verdict":        "overfitted" if r.is_overfitted else
                              "insufficient_data" if r.insufficient_data else
                              "verified"
        }
        for sym, r in results.items()
    }


@router.get("/performance/degradation")
def get_degradation():
    """Check model degradation across all symbols with enough history."""
    from app.domain.core.degradation_detector import check_all
    results = check_all()
    degraded = [s for s, r in results.items() if r.get("degraded")]
    return {
        "healthy": len(degraded) == 0,
        "degraded_symbols": degraded,
        "details": results,
    }


@router.get("/performance/monte-carlo")
def get_monte_carlo(symbol: str = None):
    """Monte Carlo significance test on win rate. p_value < 0.10 = verified edge."""
    from app.infrastructure.db.signal_history import get_monte_carlo_significance
    return get_monte_carlo_significance(symbol=symbol)


@router.get("/performance/verified-symbols")
def get_verified_symbols():
    """Return all symbols with enough history and their verification status."""
    from app.infrastructure.db.signal_history import _get_conn, get_monte_carlo_significance
    con, db = _get_conn()
    try:
        cur = con.cursor()
        cur.execute("""
            SELECT symbol, COUNT(*) as n
            FROM signal_history
            WHERE outcome IN ('win', 'loss')
            GROUP BY symbol
            HAVING COUNT(*) >= 30
            ORDER BY n DESC
        """)
        symbols = [row[0] for row in cur.fetchall()]
    finally:
        con.close()

    results = {}
    for sym in symbols:
        results[sym] = get_monte_carlo_significance(symbol=sym)

    verified = [s for s, r in results.items() if r.get("verified")]
    return {
        "total_symbols_tested": len(results),
        "verified_symbols": verified,
        "details": results,
    }
