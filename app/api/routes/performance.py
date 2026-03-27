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

from app.domain.performance.portfolio import compute_portfolio
from app.infrastructure.db.signal_history import get_evaluated_signals

@router.get("/portfolio", tags=["quant"])
def get_portfolio():
    signals = get_evaluated_signals()
    return compute_portfolio(signals)
