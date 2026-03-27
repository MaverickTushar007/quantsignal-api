from fastapi import APIRouter
from app.infrastructure.db.signal_history import get_performance, init_db
from app.domain.performance.evaluator import evaluate_open_signals

router = APIRouter()
init_db()

@router.get("/performance", tags=["quant"])
def get_performance_stats():
    return get_performance()

@router.post("/performance/evaluate", tags=["quant"])
def run_evaluation():
    results = evaluate_open_signals()
    return {"status": "done", **results}
