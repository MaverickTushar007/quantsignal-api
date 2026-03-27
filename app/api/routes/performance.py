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
    compare: bool = Query(True, description="Show filtered vs unfiltered comparison"),
):
    signals = get_evaluated_signals()
    if compare:
        return compute_dual_portfolio(signals, min_prob, min_confluence)
    return compute_portfolio(signals)

from app.domain.performance.calibration import calibrate

@router.get("/calibration", tags=["quant"])
def get_calibration():
    signals = get_evaluated_signals()
    return calibrate(signals)
