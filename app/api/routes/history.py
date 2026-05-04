from app.core.config import BASE_DIR
"""
api/history.py
Signal history + equity curve endpoint.
"""
from fastapi import APIRouter
from pathlib import Path
import json

router = APIRouter()

@router.get("/history/summary", tags=["history"])
def history_summary():
    try:
        data = json.loads((BASE_DIR / "data/signal_history.json").read_text())
        trades = data.get("trades", [])
        equity_curve = [
            {"date": t["date"], "cumulative_pnl": t["cumulative_pnl"]}
            for t in trades
        ]
        # Calculate risk metrics
        import statistics
        pnls = [t.get("pnl_pct", 0) for t in trades]
        cumulative = 0
        peak = 0
        max_dd = 0
        for p in pnls:
            cumulative += p
            if cumulative > peak:
                peak = cumulative
            dd = cumulative - peak
            if dd < max_dd:
                max_dd = dd
        sharpe = 0.0
        try:
            avg = statistics.mean(pnls)
            std = statistics.stdev(pnls)
            sharpe = round((avg / std) * (252 ** 0.5), 2) if std > 0 else 0.0
        except Exception:
            pass
        calmar = round(cumulative / abs(max_dd), 2) if max_dd != 0 else 0.0

        # Drawdown curve
        cumulative = 0
        peak = 0
        dd_curve = []
        for t in trades:
            cumulative += t.get("pnl_pct", 0)
            if cumulative > peak:
                peak = cumulative
            dd_curve.append({"date": t["date"], "drawdown": round(cumulative - peak, 3)})

        # Load cached benchmark (written by cache_signals.py / cron)
        benchmark = {}
        try:
            from pathlib import Path as _P
            import json as _j
            bp = _P("data/benchmark_cache.json")
            if bp.exists():
                benchmark = _j.loads(bp.read_text())
        except Exception as e:
            print(f"Benchmark load error: {e}")

        return {
            "total_trades":       data["total_trades"],
            "win_rate":           data["win_rate"],
            "high_conf_win_rate": data["high_conf_win_rate"],
            "high_conf_trades":   data["high_conf_trades"],
            "total_pnl":          data["total_pnl"],
            "tp_hits":            data["tp_hits"],
            "sl_hits":            data["sl_hits"],
            "generated_at":       data["generated_at"],
            "equity_curve":       equity_curve,
            "max_drawdown":       round(max_dd, 2),
            "sharpe_ratio":       sharpe,
            "calmar_ratio":       calmar,
            "dd_curve":           dd_curve,
            "benchmark":          benchmark,
        }
    except Exception as e:
        return {"error": str(e)}

@router.get("/history/trades", tags=["history"])
def history_trades(limit: int = 50, confidence: str = None, symbol: str = None):
    try:
        data = json.loads((BASE_DIR / "data/signal_history.json").read_text())
        trades = data.get("trades", [])
        if confidence:
            trades = [t for t in trades if t["confidence"].upper() == confidence.upper()]
        if symbol:
            trades = [t for t in trades if symbol.upper() in t["symbol"].upper()]
        trades = list(reversed(trades))
        return {"total": len(trades), "trades": trades[:limit]}
    except Exception as e:
        return {"error": str(e)}

@router.get("/history/montecarlo", tags=["history"])
def history_montecarlo(simulations: int = 1000):
    try:
        import random, statistics
        data = json.loads((BASE_DIR / "data/signal_history.json").read_text())
        pnls = [t.get("pnl_pct", 0) for t in data.get("trades", [])]
        n = len(pnls)
        if n < 10:
            return {"error": "Not enough trades"}

        results = []
        for _ in range(simulations):
            # Bootstrap: sample WITH replacement so totals vary
            sample = [random.choice(pnls) for _ in range(n)]
            results.append(round(sum(sample), 3))

        results.sort()
        # Percentile bands
        def pct(p):
            idx = int(p / 100 * (len(results) - 1))
            return results[idx]

        # Build percentile curves (sample 60 points for efficiency)
        step = max(1, n // 60)
        curves = {"p5": [], "p25": [], "p50": [], "p75": [], "p95": []}
        for end in range(1, n + 1, step):
            sim_ends = []
            for _ in range(200):
                sample = [random.choice(pnls) for _ in range(end)]
                sim_ends.append(round(sum(sample), 3))
            sim_ends.sort()
            def pp(p): return sim_ends[int(p / 100 * (len(sim_ends) - 1))]
            curves["p5"].append(pp(5))
            curves["p25"].append(pp(25))
            curves["p50"].append(pp(50))
            curves["p75"].append(pp(75))
            curves["p95"].append(pp(95))

        actual_final = sum(pnls)

        return {
            "simulations": simulations,
            "trades":      n,
            "actual_pnl":  round(actual_final, 2),
            "p5":          pct(5),
            "p25":         pct(25),
            "p50":         pct(50),
            "p75":         pct(75),
            "p95":         pct(95),
            "curves":      curves,
            "beat_zero":   round(sum(1 for r in results if r > 0) / len(results) * 100, 1),
        }
    except Exception as e:
        return {"error": str(e)}
