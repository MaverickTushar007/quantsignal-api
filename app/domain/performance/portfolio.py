"""
Portfolio simulation — equity curve, drawdown, Sharpe, streaks.
"""
import numpy as np
import logging

logger = logging.getLogger(__name__)

def compute_return(signal: dict) -> float:
    entry = signal["entry_price"]
    exit_ = signal["exit_price"]
    if not entry or not exit_:
        return 0.0
    if signal["direction"] == "BUY":
        return (exit_ - entry) / entry
    else:
        return (entry - exit_) / entry

def build_equity_curve(signals: list[dict]) -> list[float]:
    equity = 1.0
    curve = []
    for s in signals:
        r = compute_return(s)
        equity *= (1 + r)
        curve.append(round(equity, 6))
    return curve

def cumulative_pnl(curve: list[float]) -> float:
    return round(curve[-1] - 1, 6) if curve else 0.0

def max_drawdown(curve: list[float]) -> float:
    if not curve:
        return 0.0
    peak = curve[0]
    max_dd = 0.0
    for value in curve:
        if value > peak:
            peak = value
        dd = (peak - value) / peak if peak else 0
        max_dd = max(max_dd, dd)
    return round(max_dd, 6)

def sharpe_ratio(signals: list[dict]) -> float:
    returns = [compute_return(s) for s in signals]
    if len(returns) < 2:
        return 0.0
    mean = np.mean(returns)
    std = np.std(returns)
    return round(float(mean / std), 4) if std else 0.0

def compute_streaks(signals: list[dict]) -> dict:
    max_win = max_loss = 0
    cur_win = cur_loss = 0
    for s in signals:
        if s["outcome"] == "win":
            cur_win += 1
            cur_loss = 0
        else:
            cur_loss += 1
            cur_win = 0
        max_win = max(max_win, cur_win)
        max_loss = max(max_loss, cur_loss)
    return {"max_win_streak": max_win, "max_loss_streak": max_loss}

def compute_portfolio(signals: list[dict]) -> dict:
    evaluated = [s for s in signals if s.get("outcome") in ("win", "loss") and s.get("exit_price")]
    evaluated.sort(key=lambda x: x.get("evaluated_at") or x.get("generated_at") or "")

    if not evaluated:
        return {
            "total_evaluated": 0,
            "cumulative_pnl": None,
            "max_drawdown": None,
            "sharpe_ratio": None,
            "win_rate": None,
            "streaks": {"max_win_streak": 0, "max_loss_streak": 0},
            "equity_curve": [],
        }

    curve = build_equity_curve(evaluated)
    wins = sum(1 for s in evaluated if s["outcome"] == "win")

    return {
        "total_evaluated": len(evaluated),
        "cumulative_pnl": cumulative_pnl(curve),
        "max_drawdown": max_drawdown(curve),
        "sharpe_ratio": sharpe_ratio(evaluated),
        "win_rate": round(wins / len(evaluated), 3),
        "streaks": compute_streaks(evaluated),
        "equity_curve": curve,
    }


def filter_signals(signals: list[dict], min_prob: float = 0.65, min_confluence: int = 0, min_mtf: int = 0) -> list[dict]:
    return [
        s for s in signals
        if (s.get("probability") or 0) >= min_prob
        and (s.get("confluence_score") or 0) >= min_confluence
        and (s.get("mtf_score") or 0) >= min_mtf
    ]

def compute_dual_portfolio(signals: list[dict], min_prob: float = 0.65, min_confluence: int = 0, min_mtf: int = 0) -> dict:
    evaluated = [s for s in signals if s.get("outcome") in ("win", "loss") and s.get("exit_price")]
    evaluated.sort(key=lambda x: x.get("evaluated_at") or x.get("generated_at") or "")

    if not evaluated:
        return {"total_evaluated": 0, "all_signals": None, "filtered_signals": None}

    filtered = filter_signals(evaluated, min_prob, min_confluence, min_mtf)

    def portfolio_stats(sigs):
        if not sigs:
            return None
        curve = build_equity_curve(sigs)
        wins = sum(1 for s in sigs if s["outcome"] == "win")
        return {
            "count": len(sigs),
            "cumulative_pnl": cumulative_pnl(curve),
            "max_drawdown": max_drawdown(curve),
            "sharpe_ratio": sharpe_ratio(sigs),
            "win_rate": round(wins / len(sigs), 3),
            "streaks": compute_streaks(sigs),
            "equity_curve": curve,
        }

    return {
        "total_evaluated": len(evaluated),
        "filters_applied": {"min_probability": min_prob, "min_confluence": min_confluence},
        "all_signals": portfolio_stats(evaluated),
        "filtered_signals": portfolio_stats(filtered),
    }
