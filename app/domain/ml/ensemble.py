from app.core.config import BASE_DIR
try:
    import lightgbm as lgb
    _LGB_OK = True
except Exception:
    lgb = None

MODEL_VERSION = "1.0.0"  # bump when retrained


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
from app.domain.ml.features import (
    build_features, FEATURE_COLUMNS,
    build_features_trend, FEATURE_COLUMNS_TREND,
)

def _get_feature_set(ticker: str):
    trend = any(x in ticker for x in ["AAPL","MSFT","GOOGL","AMZN","NVDA","BTC","ETH","SOL","QQQ","SPY"])
    return (build_features_trend, FEATURE_COLUMNS_TREND) if trend else (build_features, FEATURE_COLUMNS)

MODELS_DIR   = BASE_DIR / "ml/models"
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
    volume_ratio:   float = 1.0
    data_source:    str = "unknown"
    model_version:  str = "1.0.0"

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

        _bf, _fc = _get_feature_set(ticker)
        feat = _bf(df)
        from app.domain.ml.labeling import build_triple_barrier_labels
        try:
            labeled = build_triple_barrier_labels(
                df, pt_mult=2.0, sl_mult=1.0, num_days=FORWARD_DAYS, min_ret=0.001,
            )
            labeled["bin_binary"] = (labeled["bin"] == 1).astype(int)
            valid = labeled["bin_binary"].reindex(feat.index).dropna()
            # ── frac-diff features (injected by deploy_features) ──
            try:
                from app.domain.ml.features import patch_feature_df
                feat = patch_feature_df(feat, df)
            except Exception as _fe:
                import logging as _log
                _log.getLogger(__name__).warning(f"[features] frac-diff injection failed: {_fe}")

        except Exception as _e:
            import logging
            logging.getLogger(__name__).warning(f"[labeling] triple barrier failed, using naive: {_e}")
            # TIME-SAFE fallback: label row t using close[t+FORWARD_DAYS]
            # We shift close FORWARD days into the past so each row
            # only sees prices that existed AT that row's timestamp.
            close_shifted = df["Close"].shift(FORWARD_DAYS)
            future_ret = (df["Close"] / close_shifted - 1).reindex(feat.index)
            # Trim the last FORWARD_DAYS rows — they have no valid label yet
            future_ret = future_ret.iloc[FORWARD_DAYS:]
            dynamic_thresh = max(float(future_ret.abs().quantile(0.30)), 0.001)
            labels = pd.Series(np.nan, index=feat.index)
            labels[future_ret >  dynamic_thresh] = 1
            labels[future_ret < -dynamic_thresh] = 0
            valid = labels.dropna()

        X = feat.loc[valid.index, _fc]
        y = valid.values.astype(int)
        if len(X) < 50 or len(np.unique(y)) < 2:
            return None

        split = int(len(X) * 0.8)
        X_tr, y_tr = X.iloc[:split], y[:split]

        scale_pos = int((len(y) - y.sum()) / max(y.sum(), 1))
        xgb_model = xgb.XGBClassifier(n_estimators=200, max_depth=4, learning_rate=0.05,
            subsample=0.8, colsample_bytree=0.8, eval_metric="logloss",
            scale_pos_weight=scale_pos, objective='binary:logistic',
            random_state=42, verbosity=0)
        xgb_model.fit(X_tr, y_tr)

        if not _LGB_OK:
            lgb_model = xgb_model
        else:
            lgb_base = lgb.LGBMClassifier(n_estimators=200, max_depth=4, learning_rate=0.05,
                subsample=0.8, colsample_bytree=0.8, random_state=42, verbose=-1)
            lgb_model = lgb_base
            lgb_model.fit(X_tr, y_tr)

        importance = dict(zip(FEATURE_COLUMNS, xgb_model.feature_importances_))
        top3 = dict(sorted(importance.items(), key=lambda x: x[1], reverse=True)[:3])

        # ── W4.4 OOS Sharpe gate — reject model if OOS Sharpe < 0.30 ─────────
        try:
            X_oos = X.iloc[split:]
            y_oos = y[split:]
            if len(X_oos) >= 10:
                oos_probs = xgb_model.predict_proba(X_oos)[:, 1]
                oos_preds = (oos_probs > 0.5).astype(int)
                oos_correct = (oos_preds == y_oos).astype(float)
                # Treat each prediction as a +1/-1 return
                oos_returns = oos_correct * 2 - 1
                import numpy as _np
                oos_sharpe = (
                    _np.mean(oos_returns) / (_np.std(oos_returns) + 1e-9) * _np.sqrt(252)
                )
                if oos_sharpe < 0.30:
                    import logging as _log
                    _log.getLogger(__name__).warning(
                        f"[ensemble] {ticker} OOS Sharpe {oos_sharpe:.2f} < 0.30 — model rejected"
                    )
                    return None
        except Exception as _oos_e:
            import logging as _log
            _log.getLogger(__name__).warning(f"[ensemble] OOS gate failed: {_oos_e}")

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

        _bf, _fc = _get_feature_set(ticker)
        feat   = _bf(df)
        latest = feat[_fc].iloc[[-1]]

        def safe_proba(model, X):
            import numpy as np
            # Try standard predict_proba first
            try:
                return float(model.predict_proba(X)[0, 1])
            except Exception:
                pass
            # CalibratedClassifierCV wrapping a regressor — unwrap and call directly
            try:
                inner = model.estimator if hasattr(model, "estimator") else model
                # For CalibratedClassifierCV, try calibrated_classifiers
                if hasattr(model, "calibrated_classifiers_"):
                    inner = model.calibrated_classifiers_[0].estimator
                raw = float(inner.predict(X)[0])
                return float(1 / (1 + np.exp(-raw))) if abs(raw) < 20 else float(np.clip(raw, 0, 1))
            except Exception:
                pass
            # Last resort: XGBoost Booster API
            try:
                import xgboost as xgb
                booster = model.get_booster() if hasattr(model, "get_booster") else None
                if booster is None and hasattr(model, "estimator"):
                    booster = model.estimator.get_booster()
                if booster:
                    dmat = xgb.DMatrix(X)
                    raw = float(booster.predict(dmat)[0])
                    return float(1 / (1 + np.exp(-raw))) if abs(raw) < 20 else float(np.clip(raw, 0, 1))
            except Exception:
                pass
            return 0.5  # neutral fallback
        xgb_prob = safe_proba(bundle["xgb"], latest)
        lgb_prob = safe_proba(bundle["lgb"], latest)

        sentiment_adj = (sentiment + 1) / 2
        # Load dynamic weights if available
        try:
            import json as _json
            from pathlib import Path as _Path
            _w = _json.loads((BASE_DIR / "data/model_weights.json").read_text())
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
        # Confidence thresholds aligned with _compute_conviction (HIGH is rare and earned)
        confidence = "HIGH" if prob > 0.72 or prob < 0.28 else "MEDIUM" if prob > 0.60 or prob < 0.40 else "LOW"

        close = float(df["Close"].iloc[-1])
        atr   = float((df["High"] - df["Low"]).rolling(14).mean().iloc[-1])
        tp_raw = close + 2.0 * atr if direction == "BUY" else close - 2.0 * atr
        sl_raw = close - 1.0 * atr if direction == "BUY" else close + 1.0 * atr
        # Sanity bands: cap at ±30% for TP, ±15% for SL, never inverted
        if direction == "BUY":
            tp = min(tp_raw, close * 1.30)   # max +30%
            sl = max(sl_raw, close * 0.85)   # max -15%
            tp = max(tp, close * 1.005)      # tp must be above current price
            sl = min(sl, close * 0.999)      # sl must be below current price
        else:
            tp = max(tp_raw, close * 0.70)   # max -30%
            sl = min(sl_raw, close * 1.15)   # max +15%
            tp = min(tp, close * 0.995)      # tp must be below current price
            sl = max(sl, close * 1.001)      # sl must be above current price


        # ── Stale signal filter ───────────────────────────────────────────
        # If price is already within 0.5x ATR of TP or SL, the move has
        # already happened — logging this signal inflates win rate with
        # instant fake hits. Discard it.
        tp_proximity = abs(close - tp) / atr if atr > 0 else 999
        sl_proximity = abs(close - sl) / atr if atr > 0 else 999
        if tp_proximity < 0.5 or sl_proximity < 0.5:
            import logging as _log
            _log.getLogger(__name__).warning(
                f"[stale_filter] {ticker} {direction} discarded — "
                f"tp_proximity={tp_proximity:.2f} sl_proximity={sl_proximity:.2f} ATR"
            )
            return None

        tp_dist = abs(tp - close)
        sl_dist = abs(sl - close)
        rr = tp_dist / sl_dist if sl_dist > 0 else 0

        edge_prob  = prob if direction == "BUY" else 1 - prob
        q          = 1 - edge_prob
        full_kelly = max((edge_prob * rr - q) / rr, 0) if rr > 0 else 0
        kelly_size = full_kelly * 0.25 * 100
        ev_raw = (edge_prob * tp_dist) - (q * sl_dist)
        # Normalize EV as % of current price for cross-asset comparability
        ev_pct = (ev_raw / close) * 100 if close > 0 else 0
        # Monotonicity guard: EV must be positive iff edge_prob > 0.5
        if edge_prob <= 0.5 and ev_pct > 0:
            ev_pct = -abs(ev_pct) * (0.5 - edge_prob) * 2
        ev = round(ev_pct, 4)  # EV now in % terms

        # Volume anomaly
        try:
            avg_vol = float(df["Volume"].rolling(20).mean().iloc[-1])
            cur_vol = float(df["Volume"].iloc[-1])
            volume_ratio = round(cur_vol / avg_vol, 2) if avg_vol > 0 else 1.0
        except Exception:
            volume_ratio = 1.0

        try:
            from app.domain.ml.bet_sizing import BetSizer as _BetSizer
            # For SELL, prob is P(BUY) so confidence = 1-prob
            _adj_prob = float(prob) if direction != "SELL" else 1.0 - float(prob)
            _sized = _BetSizer().size_signal({"direction": direction, "probability": _adj_prob})
            _position_size = _sized.get("position_size", 0.0)
            _kelly_raw     = _sized.get("kelly_raw", 0.0)
        except Exception:
            _position_size, _kelly_raw = 0.0, 0.0

        return SignalResult(
            ticker=ticker, direction=direction,
            probability=round(prob, 4), confidence=confidence,
            kelly_size=round(_position_size * 100 * (-1 if direction == "SELL" else 1), 2),  # negative for SELL
            expected_value=round(ev, 4),
            take_profit=round(tp, 4), stop_loss=round(sl, 4),
            current_price=round(close, 4), atr=round(atr, 4),
            risk_reward=round(rr, 2), model_agreement=round(agreement, 3),
            top_features=bundle["top_features"], was_cached=was_cached,
            volume_ratio=volume_ratio,
            data_source=__import__('app.domain.data.market', fromlist=['_FETCH_SOURCE'])._FETCH_SOURCE.get(ticker, "unknown"),
            model_version=MODEL_VERSION,
        )

    except Exception as e:
        import traceback
        traceback.print_exc()
        return None
