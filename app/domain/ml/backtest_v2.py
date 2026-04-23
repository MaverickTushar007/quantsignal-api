"""
backtest_v2.py
Walk-forward backtest using triple-barrier labels + Kelly bet sizing.
Produces: Sharpe, max drawdown, win rate, CAGR, per-trade log.
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../..")))

import numpy as np
import pandas as pd
from datetime import datetime


def run_backtest(ticker: str, n_splits: int = 5, initial_capital: float = 100_000.0) -> dict:
    from app.domain.data.market import fetch_ohlcv
    from app.domain.ml.features import build_features, FEATURE_COLUMNS, build_features_trend, FEATURE_COLUMNS_TREND
    is_trend_market = any(x in ticker for x in ["AAPL","MSFT","GOOGL","BTC","ETH","SOL","QQQ","SPY"])
    _build = build_features_trend if is_trend_market else build_features
    _cols  = FEATURE_COLUMNS_TREND if is_trend_market else FEATURE_COLUMNS
    from app.domain.ml.labeling import build_triple_barrier_labels
    from app.domain.ml.bet_sizing import BetSizer

    import xgboost as xgb
    from sklearn.calibration import CalibratedClassifierCV
    try:
        import lightgbm as lgb
        _LGB_OK = True
    except Exception:
        _LGB_OK = False

    # Force yfinance for crypto to bypass CoinGecko's 180-day limit
    if any(x in ticker for x in ["BTC", "ETH", "SOL", "USD"]):
        import yfinance as yf
        df = yf.download(ticker, period="2y", auto_adjust=True, progress=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
    else:
        df = fetch_ohlcv(ticker, period="2y")
    if df is None or len(df) < 300:
        return {"error": f"Insufficient data for {ticker}"}

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    feat    = _build(df)
    labeled = build_triple_barrier_labels(df, pt_mult=2.0, sl_mult=1.0, num_days=10, min_ret=0.001)
    labeled["bin_binary"] = (labeled["bin"] == 1).astype(int)
    valid   = labeled["bin_binary"].reindex(feat.index).dropna()

    X = feat.loc[valid.index, _cols].dropna()
    y = valid.reindex(X.index)
    close = df["Close"].reindex(X.index)

    if len(X) < 100:
        return {"error": f"Not enough labeled samples: {len(X)}"}

    split_size = len(X) // (n_splits + 1)
    sizer      = BetSizer(kelly_fraction=0.5, min_confidence=0.60, max_position=0.25)

    trades     = []
    equity     = initial_capital
    equity_curve = [equity]

    for fold in range(n_splits):
        train_end  = split_size * (fold + 1)
        test_start = train_end
        test_end   = min(test_start + split_size, len(X))

        X_tr, y_tr = X.iloc[:train_end], y.iloc[:train_end]
        X_te, y_te = X.iloc[test_start:test_end], y.iloc[test_start:test_end]
        close_te   = close.iloc[test_start:test_end]

        if len(np.unique(y_tr)) < 2 or len(X_te) == 0:
            continue

        scale_pos = int((len(y_tr) - y_tr.sum()) / max(y_tr.sum(), 1))
        xgb_base  = xgb.XGBClassifier(
            n_estimators=150, max_depth=4, learning_rate=0.05,
            subsample=0.8, colsample_bytree=0.8,
            scale_pos_weight=scale_pos, eval_metric="logloss",
            use_label_encoder=False, verbosity=0,
        )
        from sklearn.preprocessing import RobustScaler
        scaler = RobustScaler()
        X_tr_s = pd.DataFrame(scaler.fit_transform(X_tr), columns=X_tr.columns, index=X_tr.index)
        X_te_s = pd.DataFrame(scaler.transform(X_te),    columns=X_te.columns, index=X_te.index)
        model = CalibratedClassifierCV(xgb_base, cv=3, method="isotonic")
        model.fit(X_tr_s, y_tr)

        if _LGB_OK:
            lgb_base = lgb.LGBMClassifier(
                n_estimators=150, max_depth=4, learning_rate=0.05,
                subsample=0.8, colsample_bytree=0.8,
                scale_pos_weight=scale_pos, verbosity=-1,
            )
            lgb_model = CalibratedClassifierCV(lgb_base, cv=3, method="isotonic")
            lgb_model.fit(X_tr, y_tr)

        for i in range(len(X_te)):
            row   = X_te.iloc[[i]]
            price = float(close_te.iloc[i])

            row_s = pd.DataFrame(scaler.transform(row), columns=row.columns, index=row.index)
            xgb_p = float(model.predict_proba(row_s)[0, 1])
            lgb_p = float(lgb_model.predict_proba(row_s)[0, 1]) if _LGB_OK else xgb_p
            prob  = (xgb_p + lgb_p) / 2 if _LGB_OK else xgb_p

            direction = "BUY" if prob >= 0.55 else "SELL" if prob <= 0.45 else "HOLD"
            if direction == "HOLD":
                continue

            adj_prob = prob if direction == "BUY" else 1.0 - prob
            sized    = sizer.size_signal({"direction": direction, "probability": adj_prob})
            pos_size = sized.get("position_size", 0.0)

            if pos_size == 0.0:
                continue

            # Simulate: hold for 10 days, use actual future return
            future_idx = min(i + 10, len(close_te) - 1)
            future_price = float(close_te.iloc[future_idx])
            raw_ret = (future_price - price) / price
            trade_ret = raw_ret * pos_size  # pos_size is signed

            pnl      = equity * abs(pos_size) * raw_ret * (1 if direction == "BUY" else -1)
            equity  += pnl

            trades.append({
                "date":      close_te.index[i],
                "direction": direction,
                "prob":      round(prob, 4),
                "pos_size":  round(pos_size, 4),
                "ret":       round(trade_ret, 6),
                "pnl":       round(pnl, 2),
                "equity":    round(equity, 2),
                "fold":      fold,
            })
            equity_curve.append(equity)

    if not trades:
        return {"error": "No trades generated"}

    df_trades = pd.DataFrame(trades)
    returns   = df_trades["ret"].values

    total_return = (equity - initial_capital) / initial_capital
    n_days       = (df_trades["date"].iloc[-1] - df_trades["date"].iloc[0]).days
    cagr         = (1 + total_return) ** (365 / max(n_days, 1)) - 1

    daily_ret    = df_trades.set_index("date")["ret"].resample("D").sum().dropna()
    sharpe       = float(daily_ret.mean() / daily_ret.std() * np.sqrt(252)) if daily_ret.std() > 0 else 0.0

    eq_series    = pd.Series(equity_curve)
    drawdowns    = (eq_series - eq_series.cummax()) / eq_series.cummax()
    max_dd       = float(drawdowns.min())

    win_rate     = float((returns > 0).mean())
    avg_win      = float(returns[returns > 0].mean()) if (returns > 0).any() else 0.0
    avg_loss     = float(returns[returns < 0].mean()) if (returns < 0).any() else 0.0
    profit_factor = abs(avg_win * (returns > 0).sum()) / max(abs(avg_loss * (returns < 0).sum()), 1e-8)

    return {
        "ticker":         ticker,
        "n_trades":       len(trades),
        "win_rate":       round(win_rate, 4),
        "sharpe":         round(sharpe, 3),
        "cagr":           round(cagr, 4),
        "max_drawdown":   round(max_dd, 4),
        "profit_factor":  round(profit_factor, 3),
        "total_return":   round(total_return, 4),
        "final_equity":   round(equity, 2),
        "avg_win":        round(avg_win, 6),
        "avg_loss":       round(avg_loss, 6),
        "trades":         df_trades.to_dict("records"),
    }


if __name__ == "__main__":
    import json

    tickers = [
        ("RELIANCE.NS", "Indian Large Cap"),
        ("HDFCBANK.NS", "Indian Bank"),
        ("INFY.NS",     "Indian IT"),
        ("AAPL",        "US Tech"),
        ("BTC-USD",     "Crypto"),
    ]

    print(f"\n{'Ticker':<15} {'Trades':>7} {'WinRate':>8} {'Sharpe':>7} {'CAGR':>7} {'MaxDD':>8} {'PF':>6} {'FinalEq':>12}")
    print("─" * 80)

    all_results = {}
    for ticker, label in tickers:
        print(f"{ticker:<15} running...", end="\r")
        try:
            r = run_backtest(ticker, n_splits=5)
            all_results[ticker] = r
            if "error" in r:
                print(f"{ticker:<15} ERROR: {r['error']}")
            else:
                dd_pct = f"{r['max_drawdown']*100:.1f}%"
                cagr_pct = f"{r['cagr']*100:.1f}%"
                wr_pct = f"{r['win_rate']*100:.1f}%"
                print(f"{ticker:<15} {r['n_trades']:>7} {wr_pct:>8} {r['sharpe']:>7.3f} {cagr_pct:>7} {dd_pct:>8} {r['profit_factor']:>6.3f} ${r['final_equity']:>11,.0f}")
        except Exception as e:
            print(f"{ticker:<15} EXCEPTION: {e}")

    print("─" * 80)
    
    # Save full results
    out = "app/domain/ml/backtest_v2_results.json"
    with open(out, "w") as f:
        # trades list is not JSON serializable with dates, convert
        for k, v in all_results.items():
            if "trades" in v:
                v["trades"] = [
                    {**t, "date": str(t["date"])} for t in v["trades"]
                ]
        json.dump(all_results, f, indent=2)
    print(f"\nFull results saved → {out}")
