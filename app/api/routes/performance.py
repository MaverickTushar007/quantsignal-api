from fastapi import APIRouter
from app.infrastructure.db.signal_history import get_open_signals, init_db
from app.infrastructure.db.signal_history import get_performance, init_db
from app.domain.performance.evaluator import evaluate_open_signals

router = APIRouter()
init_db()

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
