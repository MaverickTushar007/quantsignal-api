"""
data/signal_history.py
Reconstructs 90-day signal history by replaying ML predictions
on historical price windows. Checks if TP/SL was hit.
Stores results in signal_history.json for equity curve display.
"""
import json, time
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime, timezone, timedelta

HISTORY_CACHE = Path("data/signal_history.json")
LOOKBACK_DAYS = 90
FORWARD_DAYS  = 5   # days to check TP/SL outcome

def simulate_history(symbols: list, max_symbols: int = 30) -> list:
    """
    Simulate signal history for a subset of symbols.
    Returns list of trade records.
    """
    from app.domain.data.market import fetch_ohlcv
    from app.domain.ml.features import build_features
    from app.domain.ml.ensemble import predict, FORWARD_DAYS as FWD

    # Use high-volume liquid symbols for reliable history
    priority = [
        'BTC-USD', 'ETH-USD', 'NIFTY50', '^NSEI',
        'RELIANCE.NS', 'TCS.NS', 'INFY.NS', 'HDFCBANK.NS',
        'ICICIBANK.NS', 'WIPRO.NS', 'AAPL', 'NVDA', 'MSFT',
        'GOOGL', 'AMZN', 'META', 'TSLA', 'GC=F', 'GLD', 'SLV',
        'BAJFINANCE.NS', 'KOTAKBANK.NS', 'AXISBANK.NS', 'SBIN.NS',
        'MARUTI.NS', 'TITAN.NS', 'LT.NS', 'ITC.NS', 'ASIANPAINT.NS',
        'ADANIENT.NS',
    ]
    # Filter to only symbols we have in universe
    symbols_to_run = [s for s in priority if s in symbols][:max_symbols]

    all_trades = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)

    for i, sym in enumerate(symbols_to_run):
        print(f"[{i+1}/{len(symbols_to_run)}] Simulating {sym}...", end=" ", flush=True)
        try:
            df = fetch_ohlcv(sym, period="2y")
            if df is None or len(df) < 120:
                print("skip — insufficient data")
                continue

            feat = build_features(df)
            if len(feat) < 80:
                print("skip — insufficient features")
                continue

            # Get sentiment (neutral for historical)
            sentiment = 0.0

            trades_for_sym = 0
            _wf_bundle = None
            _wf_step   = 0
            # Slide window: every 5 days to avoid overfitting
            indices = list(range(300, len(df) - FORWARD_DAYS - 1, 5))

            for idx in indices:
                try:
                    bar_date = df.index[idx]
                    # Skip if before cutoff
                    if hasattr(bar_date, 'tzinfo') and bar_date.tzinfo:
                        if bar_date < cutoff:
                            continue
                    
                    # Walk-forward: reuse frozen bundle, retrain every 60 steps
                    df_window   = df.iloc[:idx+1]
                    feat_window = build_features(df_window)
                    if len(feat_window) < 150:
                        continue

                    if _wf_bundle is None or _wf_step % 60 == 0:
                        from app.domain.ml.ensemble import train as _train
                        _new = _train(sym, df_window)
                        if _new is not None:
                            _wf_bundle = _new
                    _wf_step += 1
                    if _wf_bundle is None:
                        continue

                    # Predict inline — no file I/O, no retrain
                    import numpy as np
                    from app.domain.ml.ensemble import FEATURE_COLUMNS
                    _feat  = feat_window[FEATURE_COLUMNS].iloc[[-1]]
                    _xp    = float(_wf_bundle["xgb"].predict_proba(_feat)[0, 1])
                    _lp    = float(_wf_bundle["lgb"].predict_proba(_feat)[0, 1])
                    _prob  = round(max(0.01, min(0.99, (_xp + _lp) / 2)), 4)

                    class _R: pass
                    ml = _R()
                    ml.probability = _prob
                    ml.direction   = "BUY" if _prob > 0.55 else ("SELL" if _prob < 0.45 else "HOLD")
                    ml.confidence  = "HIGH" if abs(_prob - 0.5) > 0.15 else "MEDIUM"
                    try:
                        _atr = df_window["Close"].diff().abs().rolling(14).mean().iloc[-1]
                        ml.atr = float(_atr) if not np.isnan(_atr) else float(df_window["Close"].iloc[-1]) * 0.02
                    except Exception:
                        ml.atr = float(df_window["Close"].iloc[-1]) * 0.02
                    ml = predict(sym, df_window, sentiment)
                    if ml is None:
                        continue

                    # Skip HOLD signals
                    if ml.direction == "HOLD":
                        continue

                    entry_price = float(df['Close'].iloc[idx])
                    if entry_price <= 0:
                        continue

                    # Calculate TP/SL from ATR
                    atr = ml.atr if ml.atr > 0 else entry_price * 0.02
                    if ml.direction == "BUY":
                        take_profit = entry_price + (atr * 2.0)
                        stop_loss   = entry_price - (atr * 1.0)
                    else:  # SELL
                        take_profit = entry_price - (atr * 2.0)
                        stop_loss   = entry_price + (atr * 1.0)

                    # Check outcome over next FORWARD_DAYS
                    future_slice = df.iloc[idx+1 : idx+1+FORWARD_DAYS]
                    outcome = "EXPIRED"
                    exit_price = float(future_slice['Close'].iloc[-1])
                    pnl_pct = 0.0

                    for _, fbar in future_slice.iterrows():
                        high = float(fbar['High'])
                        low  = float(fbar['Low'])
                        if ml.direction == "BUY":
                            if high >= take_profit:
                                outcome    = "TP_HIT"
                                exit_price = take_profit
                                pnl_pct    = (take_profit - entry_price) / entry_price * 100
                                break
                            if low <= stop_loss:
                                outcome    = "SL_HIT"
                                exit_price = stop_loss
                                pnl_pct    = (stop_loss - entry_price) / entry_price * 100
                                break
                        else:  # SELL
                            if low <= take_profit:
                                outcome    = "TP_HIT"
                                exit_price = take_profit
                                pnl_pct    = (entry_price - take_profit) / entry_price * 100
                                break
                            if high >= stop_loss:
                                outcome    = "SL_HIT"
                                exit_price = stop_loss
                                pnl_pct    = (entry_price - stop_loss) / entry_price * 100
                                break

                    if outcome == "EXPIRED":
                        pnl_pct = (exit_price - entry_price) / entry_price * 100
                        if ml.direction == "SELL":
                            pnl_pct = -pnl_pct

                    all_trades.append({
                        "symbol":      sym,
                        "date":        bar_date.strftime("%Y-%m-%d") if hasattr(bar_date, 'strftime') else str(bar_date)[:10],
                        "direction":   ml.direction,
                        "confidence":  ml.confidence,
                        "probability": round(ml.probability, 3),
                        "entry":       round(entry_price, 4),
                        "take_profit": round(take_profit, 4),
                        "stop_loss":   round(stop_loss, 4),
                        "exit":        round(exit_price, 4),
                        "outcome":     outcome,
                        "pnl_pct":     round(pnl_pct, 3),
                    })
                    trades_for_sym += 1

                except Exception:
                    continue

            print(f"✓ {trades_for_sym} trades")

        except Exception as e:
            print(f"✗ {e}")
        time.sleep(0.3)

    # Sort by date
    all_trades.sort(key=lambda x: x["date"])

    # Calculate cumulative P&L (equal weight per trade)
    cumulative = 0.0
    for t in all_trades:
        cumulative += t["pnl_pct"]
        t["cumulative_pnl"] = round(cumulative, 3)

    # Summary stats
    total   = len(all_trades)
    wins    = sum(1 for t in all_trades if t["outcome"] == "TP_HIT")
    losses  = sum(1 for t in all_trades if t["outcome"] == "SL_HIT")
    high_conf = [t for t in all_trades if t["confidence"] == "HIGH"]
    hc_wins   = sum(1 for t in high_conf if t["outcome"] == "TP_HIT")

    summary = {
        "generated_at":       datetime.now(timezone.utc).isoformat(),
        "total_trades":       total,
        "tp_hits":            wins,
        "sl_hits":            losses,
        "win_rate":           round(wins / total * 100, 1) if total > 0 else 0,
        "high_conf_trades":   len(high_conf),
        "high_conf_win_rate": round(hc_wins / len(high_conf) * 100, 1) if high_conf else 0,
        "total_pnl":          round(cumulative, 2),
        "trades":             all_trades,
    }

    HISTORY_CACHE.write_text(json.dumps(summary, indent=2))
    print(f"\n✅ History built: {total} trades | WR: {summary['win_rate']}% | Total P&L: {cumulative:.1f}%")
    return all_trades

if __name__ == "__main__":
    import sys
    sys.path.insert(0, '.')
    from app.domain.data.universe import TICKERS
    symbols = {t["symbol"] for t in TICKERS}
    simulate_history(list(symbols))
