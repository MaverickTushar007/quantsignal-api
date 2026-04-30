"""
Retrains all 186 models using current feature set.
Runs in parallel — ~5 min instead of 30.
"""
import sys, os
sys.path.insert(0, os.path.expanduser("~/Desktop/quantsignal-api"))

import warnings
warnings.filterwarnings("ignore")

import joblib
import numpy as np
import pandas as pd
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from app.domain.data.universe import TICKERS
from app.domain.data.market import fetch_ohlcv
from app.domain.ml.features import build_features, FEATURE_COLUMNS

MODEL_DIR = Path.home() / "Desktop/quantsignal-api/ml/models"
LOG_DIR   = Path.home() / "Desktop/quantsignal-api/logs"
LOG_DIR.mkdir(exist_ok=True)

def make_labels(df: pd.DataFrame, horizon=5, threshold=0.01):
    fwd = df["Close"].pct_change(horizon).shift(-horizon)
    labels = pd.Series("HOLD", index=df.index)
    labels[fwd >  threshold] = "BUY"
    labels[fwd < -threshold] = "SELL"
    return labels

def train_one(ticker_info):
    sym = ticker_info["symbol"]
    try:
        df = fetch_ohlcv(sym, period="3y")
        if df is None or len(df) < 150:
            return sym, "skip", f"insufficient data ({len(df) if df else 0} rows)"

        feats  = build_features(df)
        labels = make_labels(df).reindex(feats.index).dropna()
        feats  = feats.loc[labels.index][FEATURE_COLUMNS].dropna()
        labels = labels.loc[feats.index]

        if len(feats) < 100:
            return sym, "skip", f"too few clean rows: {len(feats)}"

        if labels.nunique() < 2:
            return sym, "skip", "only one class in labels"

        from xgboost import XGBClassifier
        from sklearn.preprocessing import LabelEncoder
        import numpy as np

        le = LabelEncoder()
        y  = le.fit_transform(labels)
        n_classes = len(le.classes_)

        model = XGBClassifier(
            n_estimators=200,
            max_depth=4,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            objective="multi:softprob",
            num_class=n_classes,
            eval_metric="mlogloss",
            verbosity=0,
            n_jobs=1,
        )
        model.fit(feats, y)

        # Attach metadata
        model.label_encoder_    = le
        model.trained_at_       = datetime.now(timezone.utc).isoformat()
        model.symbol_           = sym

        out = MODEL_DIR / f"{sym.replace('-','_').replace('^','_')}.pkl"
        # Use symbol as-is to match existing naming
        out = MODEL_DIR / f"{sym}.pkl"
        joblib.dump({"xgb": model, "lgb": model, "le": le}, out)
        return sym, "ok", f"{len(feats)} rows, classes={list(le.classes_)}"

    except Exception as e:
        return sym, "error", str(e)

if __name__ == "__main__":
    print(f"Retraining {len(TICKERS)} models with {len(FEATURE_COLUMNS)} features...")
    print(f"Features: {FEATURE_COLUMNS}\n")

    ok = skipped = errors = 0
    results = []

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(train_one, t): t["symbol"] for t in TICKERS}
        for i, fut in enumerate(as_completed(futures)):
            sym, status, msg = fut.result()
            results.append((sym, status, msg))
            if status == "ok":
                ok += 1
                print(f"[{i+1}/{len(TICKERS)}] ✓ {sym}: {msg}")
            elif status == "skip":
                skipped += 1
                print(f"[{i+1}/{len(TICKERS)}] ⚠ {sym}: {msg}")
            else:
                errors += 1
                print(f"[{i+1}/{len(TICKERS)}] ✗ {sym}: {msg}")

    print(f"\n{'='*60}")
    print(f"Done: {ok} trained | {skipped} skipped | {errors} errors")
    print(f"Models saved to: {MODEL_DIR}")

    # Save summary log
    log = LOG_DIR / "retrain_log.txt"
    with open(log, "w") as f:
        f.write(f"Retrained at {datetime.now(timezone.utc).isoformat()}\n")
        f.write(f"Features: {FEATURE_COLUMNS}\n\n")
        for sym, status, msg in sorted(results):
            f.write(f"{status:6} {sym}: {msg}\n")
    print(f"Log saved: {log}")
