"""
ml/auto_retrain.py
Auto-retrains models with win rate below threshold.
Called after each daily cache rebuild — removes humans from the loop.
Karpathy principle: verifiable metric (win rate) → auto-improve.
"""
import sys, json, pickle, numpy as np, time
from pathlib import Path
from typing import List, Tuple

WIN_RATE_THRESHOLD = 60.0  # retrain anything below this
MAX_RETRAIN_PER_RUN = 10   # cap to avoid Railway timeout

def score_model(sym: str) -> float:
    """Score a model's win rate on recent data. Returns -1 if can't score."""
    try:
        from ml.ensemble import _model_path, FORWARD_DAYS, FEATURE_COLUMNS
        from data.market import fetch_ohlcv
        from ml.features import build_features

        path = _model_path(sym)
        if not path.exists():
            return -1.0

        with open(path, 'rb') as f:
            bundle = pickle.load(f)

        df = fetch_ohlcv(sym)
        if df is None or len(df) < 100:
            return -1.0

        feat = build_features(df)
        if len(feat) < 80:
            return -1.0

        future_ret = df["Close"].pct_change(FORWARD_DAYS).shift(-FORWARD_DAYS).reindex(feat.index)
        dynamic_thresh = max(float(future_ret.abs().quantile(0.30)), 0.001)
        probs = bundle["xgb"].predict_proba(feat[FEATURE_COLUMNS])[:, 1]

        wins, total = 0, 0
        for prob, ret in zip(probs, future_ret.values):
            if np.isnan(ret):
                continue
            actual = 1 if ret > dynamic_thresh else 0 if ret < -dynamic_thresh else -1
            if actual == -1:
                continue
            pred = 1 if prob > 0.5 else 0
            if pred == actual:
                wins += 1
            total += 1

        return round(wins / total * 100, 1) if total > 0 else -1.0

    except Exception as e:
        print(f"  Score failed for {sym}: {e}")
        return -1.0

def retrain_model(sym: str) -> bool:
    """Force retrain a model by deleting its pkl and regenerating."""
    try:
        from ml.ensemble import _model_path
        from data.market import fetch_ohlcv
        from ml.ensemble import train

        # Delete stale model to force full retrain
        path = _model_path(sym)
        if path.exists():
            path.unlink()

        df = fetch_ohlcv(sym)
        if df is None or len(df) < 100:
            return False

        bundle = train(sym, df)
        return bundle is not None

    except Exception as e:
        print(f"  Retrain failed for {sym}: {e}")
        return False

def run_auto_retrain(symbols: List[str]) -> dict:
    """
    Main entry point — score all models, retrain weak ones.
    Returns summary dict.
    """
    print(f"\n🔬 Auto-retrain scan: {len(symbols)} models...")
    start = time.time()

    weak, strong, skipped = [], [], []

    # Score all models
    for sym in symbols:
        wr = score_model(sym)
        if wr < 0:
            skipped.append(sym)
        elif wr < WIN_RATE_THRESHOLD:
            weak.append((sym, wr))
            print(f"  ⚠️  {sym}: {wr}% — below threshold")
        else:
            strong.append((sym, wr))

    # Sort weakest first, cap at MAX_RETRAIN_PER_RUN
    weak.sort(key=lambda x: x[1])
    to_retrain = weak[:MAX_RETRAIN_PER_RUN]

    print(f"\n📊 Scores: {len(strong)} strong, {len(weak)} weak, {len(skipped)} skipped")
    print(f"🔄 Retraining {len(to_retrain)} models...")

    retrained, failed = [], []
    for sym, old_wr in to_retrain:
        print(f"  Retraining {sym} (was {old_wr}%)...", end=" ", flush=True)
        success = retrain_model(sym)
        if success:
            new_wr = score_model(sym)
            improvement = round(new_wr - old_wr, 1) if new_wr > 0 else 0
            retrained.append({"symbol": sym, "old_wr": old_wr, "new_wr": new_wr, "improvement": improvement})
            print(f"✓ {old_wr}% → {new_wr}% (+{improvement}%)")
        else:
            failed.append(sym)
            print(f"✗ failed")
        time.sleep(0.5)

    elapsed = round(time.time() - start, 1)
    summary = {
        "scanned": len(symbols),
        "weak": len(weak),
        "strong": len(strong),
        "retrained": len(retrained),
        "failed": len(failed),
        "improvements": retrained,
        "elapsed_seconds": elapsed,
    }

    print(f"\n✅ Auto-retrain complete in {elapsed}s")
    print(f"   Retrained: {len(retrained)} | Failed: {len(failed)}")
    if retrained:
        avg_improvement = np.mean([r["improvement"] for r in retrained])
        print(f"   Avg improvement: +{avg_improvement:.1f}%")

    return summary
