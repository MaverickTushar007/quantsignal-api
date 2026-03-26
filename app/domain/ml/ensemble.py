try:
    import lightgbm as lgb
    _LGB_OK = True
except Exception:
    lgb = None
    _LGB_OK = False

import numpy as np
import pandas as pd
import pickle
try:
    import lightgbm as _lgb_test
    _LGB_OK = True
except Exception:
    _LGB_OK = False
from pathlib import Path
from dataclasses import dataclass
from typing import Optional
from datetime import datetime, timezone
from app.domain.ml.features import build_features, FEATURE_COLUMNS

MODELS_DIR   = Path("ml/models")
MODELS_DIR.mkdir(exist_ok=True)
RETRAIN_DAYS = 7
FORWARD_DAYS = 5
RETURN_THRESH= 0.02

@dataclass
class SignalResult:
    ticker: str
    direction: str
    probability: float
    confidence: str
    kelly_size: float
    expected_value: float
    take_profit: float
    stop_loss: float
    current_price: float
    atr: float
    risk_reward: float
    model_agreement: float
    top_features: dict
    was_cached: bool
    volume_ratio: float = 1.0

def _model_path(ticker):
    safe = ticker.replace("=","_").replace("^","_").replace("-","_")
    return MODELS_DIR / f"{safe}.pkl"

def _is_stale(path):
    if not path.exists(): return True
    return (datetime.now().timestamp() - path.stat().st_mtime) / 86400 > RETRAIN_DAYS

def train(ticker, df):
    try:
        # Flatten MultiIndex columns (yf.download returns these)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        import xgboost as xgb
        from sklearn.calibration import CalibratedClassifierCV
        try:
            import lightgbm as lgb
            _LGB_OK = True
        except Exception:
            lgb = None
            _LGB_OK = False

        feat = build_features(df)
        future_ret = df["Close"].pct_change(FORWARD_DAYS).shift(-FORWARD_DAYS).reindex(feat.index)
        # Dynamic threshold — use 30th percentile of abs returns so we always get enough samples
        dynamic_thresh = max(float(future_ret.abs().quantile(0.30)), 0.001)
        labels = pd.Series(np.nan, index=feat.index)
        labels[future_ret >  dynamic_thresh] = 1
        labels[future_ret < -dynamic_thresh] = 0
        valid = labels.dropna()

        X = feat.loc[valid.index, FEATURE_COLUMNS]
        y = valid.values.astype(int)
        if len(X) < 150 or len(np.unique(y)) < 2:
            return None

        split = int(len(X) * 0.8)
        X_tr, y_tr = X.iloc[:split], y[:split]

        xgb_base = xgb.XGBClassifier(n_estimators=200, max_depth=4, learning_rate=0.05,
            subsample=0.8, colsample_bytree=0.8, eval_metric="logloss",
            random_state=42, verbosity=0)
        xgb_model = CalibratedClassifierCV(xgb_base, cv=3, method="isotonic")
        xgb_model.fit(X_tr, y_tr)

        if not _LGB_OK:
            lgb_model = xgb_model
        else:
            lgb_base = lgb.LGBMClassifier(n_estimators=200, max_depth=4, learning_rate=0.05,
                subsample=0.8, colsample_bytree=0.8, random_state=42, verbose=-1)
            lgb_model = CalibratedClassifierCV(lgb_base, cv=3, method="isotonic")
            lgb_model.fit(X_tr, y_tr)

        importance = dict(zip(FEATURE_COLUMNS, xgb_model.calibrated_classifiers_[0].estimator.feature_importances_))
        top3 = dict(sorted(importance.items(), key=lambda x: x[1], reverse=True)[:3])

        bundle = {"xgb": xgb_model, "lgb": lgb_model, "top_features": top3,
                  "trained_at": datetime.now(timezone.utc).isoformat()}
        with open(_model_path(ticker), "wb") as f:
            pickle.dump(bundle, f)
        return bundle

    except Exception as e:
        import traceback
        traceback.print_exc()
        return None

def predict(ticker, df, sentiment=0.0):
    try:
        path = _model_path(ticker)
        was_cached = False
        if _is_stale(path):
            bundle = train(ticker, df)
        else:
            with open(path, "rb") as f:
                bundle = pickle.load(f)
            was_cached = True

        if bundle is None:
            return None

        feat   = build_features(df)
        latest = feat[FEATURE_COLUMNS].iloc[[-1]]

        xgb_prob = float(bundle["xgb"].predict_proba(latest)[0, 1])
        lgb_prob = float(bundle["lgb"].predict_proba(latest)[0, 1])

        sentiment_adj = (sentiment + 1) / 2
        # Load dynamic weights if available
        try:
            import json as _json
            from pathlib import Path as _Path
            _w = _json.loads(_Path("data/model_weights.json").read_text())
            _xw = _w.get("xgb_weight", 0.45)
            _lw = _w.get("lgb_weight", 0.45)
        except Exception:
            _xw, _lw = 0.45, 0.45

        raw_prob = _xw * xgb_prob + _lw * lgb_prob + 0.10 * sentiment_adj

        # Macro + funding rate regime adjustment
        try:
            from app.domain.data.macro import get_macro_features
            from app.domain.data.funding import get_funding_features
            macro = get_macro_features()
            funding = get_funding_features(ticker)

            # Macro bearish conditions pull prob toward 0.5
            bearish_count = sum([
                macro.get("high_fear", 0),
                macro.get("recession_signal", 0),
                macro.get("rate_hike_regime", 0),
                macro.get("inflation_high", 0),
            ])
            macro_pull = bearish_count * 0.015
            prob = raw_prob + (0.5 - raw_prob) * macro_pull

            # Funding rate adjustment for crypto
            funding_signal = funding.get("funding_signal", 0.0)
            if funding_signal != 0.0:
                prob = prob + funding_signal * 0.02

            # Fear & Greed contrarian adjustment
            try:
                from app.domain.data.fear_greed import get_fear_greed
                fg = get_fear_greed()
                contrarian = fg.get("contrarian_signal", 0.0)
                prob = prob + contrarian * 0.015
            except Exception:
                pass

            # Long/Short positioning contrarian adjustment
            try:
                from app.domain.data.positioning import get_positioning
                pos = get_positioning(ticker)
                pos_signal = pos.get("positioning_signal", 0.0)
                # Crowded positioning = 1.5% contrarian adjustment
                prob = prob + pos_signal * 0.015
            except Exception:
                pass

            prob = round(max(0.01, min(0.99, prob)), 4)
        except Exception:
            prob = raw_prob

        direction = "BUY" if prob >= 0.55 else "SELL" if prob <= 0.45 else "HOLD"
        agreement = 1.0 - abs(xgb_prob - lgb_prob) if _LGB_OK else 1.0
        # Store per-model probs for dynamic weight tracking
        try:
            import json as _j, time as _t
            from pathlib import Path as _p
            _log = _p("data/model_prob_log.jsonl")
            _entry = _j.dumps({"ts": _t.time(), "sym": ticker,
                               "xgb": round(xgb_prob,4), "lgb": round(lgb_prob,4)}) + "\n"
            with open(_log, "a") as _f:
                _f.write(_entry)
        except Exception:
            pass
        confidence = "HIGH" if prob > 0.65 or prob < 0.35 else "MEDIUM" if prob > 0.55 or prob < 0.45 else "LOW"

        close = float(df["Close"].iloc[-1])
        atr   = float((df["High"] - df["Low"]).rolling(14).mean().iloc[-1])
        tp = close + 2.0 * atr if direction == "BUY" else close - 2.0 * atr
        sl = close - 1.0 * atr if direction == "BUY" else close + 1.0 * atr

        tp_dist = abs(tp - close)
        sl_dist = abs(sl - close)
        rr = tp_dist / sl_dist if sl_dist > 0 else 0

        edge_prob  = prob if direction == "BUY" else 1 - prob
        q          = 1 - edge_prob
        full_kelly = max((edge_prob * rr - q) / rr, 0) if rr > 0 else 0
        kelly_size = full_kelly * 0.25 * 100
        ev = (edge_prob * tp_dist) - (q * sl_dist)

        # Volume anomaly
        try:
            avg_vol = float(df["Volume"].rolling(20).mean().iloc[-1])
            cur_vol = float(df["Volume"].iloc[-1])
            volume_ratio = round(cur_vol / avg_vol, 2) if avg_vol > 0 else 1.0
        except Exception:
            volume_ratio = 1.0

        return SignalResult(
            ticker=ticker, direction=direction,
            probability=round(prob, 4), confidence=confidence,
            kelly_size=round(kelly_size, 2), expected_value=round(ev, 4),
            take_profit=round(tp, 4), stop_loss=round(sl, 4),
            current_price=round(close, 4), atr=round(atr, 4),
            risk_reward=round(rr, 2), model_agreement=round(agreement, 3),
            top_features=bundle["top_features"], was_cached=was_cached,
            volume_ratio=volume_ratio,
        )

    except Exception as e:
        import traceback
        traceback.print_exc()
        return None
