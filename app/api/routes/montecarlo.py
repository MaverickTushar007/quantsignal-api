"""
api/montecarlo.py
Monte Carlo simulation — 1000 reshuffles of signal history.
"""
from fastapi import APIRouter
from pathlib import Path
import json, random, statistics

router = APIRouter()

@router.get("/history/montecarlo", tags=["history"])
def monte_carlo(simulations: int = 1000):
    try:
        data = json.loads(Path("data/signal_history.json").read_text())
        trades = data.get("trades", [])
        pnls = [t.get("pnl_pct", 0) for t in trades]

        if len(pnls) < 10:
            return {"error": "Not enough trades"}

        # Run simulations
        final_pnls = []
        max_dds = []
        percentile_curves = {5: [], 25: [], 50: [], 75: [], 95: []}

        all_curves = []
        for _ in range(simulations):
            shuffled = pnls.copy()
            random.shuffle(shuffled)

            cumulative = 0
            peak = 0
            max_dd = 0
            curve = []
            for p in shuffled:
                cumulative += p
                if cumulative > peak:
                    peak = cumulative
                dd = cumulative - peak
                if dd < max_dd:
                    max_dd = dd
                curve.append(round(cumulative, 3))

            final_pnls.append(round(cumulative, 3))
            max_dds.append(round(max_dd, 3))
            all_curves.append(curve)

        # Sort by final P&L
        final_pnls_sorted = sorted(final_pnls)
        n = len(final_pnls_sorted)

        def pct(arr, p):
            idx = int(len(arr) * p / 100)
            return arr[min(idx, len(arr)-1)]

        # Sample 50 curves for fan chart (evenly spaced by final pnl)
        sorted_curves = [c for _, c in sorted(zip(final_pnls, all_curves))]
        step = max(1, len(sorted_curves) // 50)
        fan_curves = sorted_curves[::step][:50]

        # Downsample each curve to 50 points for frontend
        def downsample(curve, n=50):
            if len(curve) <= n:
                return curve
            step = len(curve) / n
            return [curve[int(i * step)] for i in range(n)]

        fan_sampled = [downsample(c) for c in fan_curves]

        # Actual result curve
        actual_cumulative = 0
        actual_curve = []
        for p in pnls:
            actual_cumulative += p
            actual_curve.append(round(actual_cumulative, 3))

        return {
            "simulations":     simulations,
            "trade_count":     len(pnls),
            "actual_pnl":      round(actual_cumulative, 3),
            "percentiles": {
                "p5":  round(pct(final_pnls_sorted, 5), 2),
                "p25": round(pct(final_pnls_sorted, 25), 2),
                "p50": round(pct(final_pnls_sorted, 50), 2),
                "p75": round(pct(final_pnls_sorted, 75), 2),
                "p95": round(pct(final_pnls_sorted, 95), 2),
            },
            "max_dd_percentiles": {
                "p50": round(pct(sorted(max_dds), 50), 2),
                "p95": round(pct(sorted(max_dds), 95), 2),
            },
            "probability_positive": round(sum(1 for p in final_pnls if p > 0) / len(final_pnls) * 100, 1),
            "fan_curves":      fan_sampled,
            "actual_curve":    downsample(actual_curve),
        }

    except Exception as e:
        return {"error": str(e)}
