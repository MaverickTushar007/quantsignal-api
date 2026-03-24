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
        data = json.loads(Path("data/signal_history.json").read_text())
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
        data = json.loads(Path("data/signal_history.json").read_text())
        trades = data.get("trades", [])
        if confidence:
            trades = [t for t in trades if t["confidence"].upper() == confidence.upper()]
        if symbol:
            trades = [t for t in trades if symbol.upper() in t["symbol"].upper()]
        trades = list(reversed(trades))
        return {"total": len(trades), "trades": trades[:limit]}
    except Exception as e:
        return {"error": str(e)}
