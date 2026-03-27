"""
Confidence calibration — maps model probability bins to actual win rates.
Tells you whether your model's confidence scores are trustworthy.
"""
import numpy as np

def calibrate(signals: list[dict], bins: int = 5) -> dict:
    """
    Bucket signals by probability, compute actual win rate per bucket.
    A well-calibrated model: high prob bucket → high win rate.
    """
    evaluated = [
        s for s in signals
        if s.get("outcome") in ("win", "loss")
        and s.get("probability") is not None
    ]

    if len(evaluated) < 3:
        return {"error": "need at least 3 evaluated signals with probability scores", "count": len(evaluated)}

    probs = [s["probability"] for s in evaluated]
    min_p, max_p = min(probs), max(probs)
    bin_size = (max_p - min_p) / bins if max_p > min_p else 0.2

    buckets = {}
    for s in evaluated:
        p = s["probability"]
        bucket_idx = min(int((p - min_p) / bin_size), bins - 1) if bin_size else 0
        label = f"{min_p + bucket_idx * bin_size:.2f}-{min_p + (bucket_idx+1) * bin_size:.2f}"
        if label not in buckets:
            buckets[label] = {"wins": 0, "losses": 0, "signals": []}
        buckets[label]["signals"].append(p)
        if s["outcome"] == "win":
            buckets[label]["wins"] += 1
        else:
            buckets[label]["losses"] += 1

    result = []
    for label, data in sorted(buckets.items()):
        total = data["wins"] + data["losses"]
        result.append({
            "prob_range": label,
            "count": total,
            "wins": data["wins"],
            "actual_win_rate": round(data["wins"] / total, 3) if total else None,
            "avg_predicted_prob": round(np.mean(data["signals"]), 3),
        })

    # Calibration score: correlation between predicted prob and actual win rate
    if len(result) >= 2:
        predicted = [r["avg_predicted_prob"] for r in result if r["actual_win_rate"] is not None]
        actual = [r["actual_win_rate"] for r in result if r["actual_win_rate"] is not None]
        corr = float(np.corrcoef(predicted, actual)[0, 1]) if len(predicted) >= 2 else None
    else:
        corr = None

    return {
        "total_evaluated": len(evaluated),
        "calibration_correlation": round(corr, 3) if corr is not None else None,
        "interpretation": _interpret(corr),
        "buckets": result,
    }

def _interpret(corr) -> str:
    if corr is None:
        return "insufficient data"
    if corr > 0.7:
        return "well calibrated — model confidence is trustworthy"
    if corr > 0.3:
        return "partially calibrated — use with caution"
    if corr > -0.3:
        return "uncalibrated — probability scores are noise"
    return "inverted — high probability predicts losses (regime mismatch likely)"
