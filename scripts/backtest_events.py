"""
backtest_events.py
Measures real price impact of NFP/FOMC/CPI on your asset universe.
Outputs ATR multipliers to use in Track 5 stop widening.
"""
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

# ── Known high-impact event dates (last 2 years) ──────────────────────────
# NFP = first Friday of each month
# FOMC = from Fed calendar
# CPI = ~middle of each month

EVENTS = {
    "NFP": [
        "2023-01-06","2023-02-03","2023-03-10","2023-04-07","2023-05-05",
        "2023-06-02","2023-07-07","2023-08-04","2023-09-01","2023-10-06",
        "2023-11-03","2023-12-08","2024-01-05","2024-02-02","2024-03-08",
        "2024-04-05","2024-05-03","2024-06-07","2024-07-05","2024-08-02",
        "2024-09-06","2024-10-04","2024-11-01","2024-12-06","2025-01-10",
        "2025-02-07","2025-03-07",
    ],
    "FOMC": [
        "2023-02-01","2023-03-22","2023-05-03","2023-06-14","2023-07-26",
        "2023-09-20","2023-11-01","2023-12-13","2024-01-31","2024-03-20",
        "2024-05-01","2024-06-12","2024-07-31","2024-09-18","2024-11-07",
        "2024-12-18","2025-01-29","2025-03-19",
    ],
    "CPI": [
        "2023-01-12","2023-02-14","2023-03-14","2023-04-12","2023-05-10",
        "2023-06-13","2023-07-12","2023-08-10","2023-09-13","2023-10-12",
        "2023-11-14","2023-12-12","2024-01-11","2024-02-13","2024-03-12",
        "2024-04-10","2024-05-15","2024-06-12","2024-07-11","2024-08-14",
        "2024-09-11","2024-10-10","2024-11-13","2024-12-11","2025-01-15",
        "2025-02-12","2025-03-12",
    ],
}

ASSETS = ["BTC-USD", "ETH-USD", "SPY", "QQQ", "GLD"]

def get_price_data(ticker, start="2023-01-01", end="2025-04-01"):
    df = yf.download(ticker, start=start, end=end, auto_adjust=True, progress=False)
    df.index = pd.to_datetime(df.index).tz_localize(None)
    return df

def compute_atr(df, period=14):
    high = df["High"]
    low  = df["Low"]
    close= df["Close"].shift(1)
    tr   = pd.concat([high-low, (high-close).abs(), (low-close).abs()], axis=1).max(axis=1)
    return tr.rolling(period).mean()

def analyze_event_impact(df, event_dates, label):
    atr = compute_atr(df)
    results = []
    for d in event_dates:
        try:
            dt = pd.Timestamp(d)
            if dt not in df.index:
                # find nearest trading day
                idx = df.index.searchsorted(dt)
                if idx >= len(df): continue
                dt = df.index[idx]
            
            pos = df.index.get_loc(dt)
            if pos < 20 or pos >= len(df)-1: continue
            
            # Event day range vs 20-day average range
            event_range = float(df["High"].iloc[pos] - df["Low"].iloc[pos])
            avg_atr     = float(atr.iloc[pos-1])  # ATR before event
            
            if avg_atr <= 0: continue
            
            multiplier = event_range / avg_atr
            
            # How often did price breach normal ATR-based TP/SL?
            close_before = float(df["Close"].iloc[pos-1])
            normal_tp    = close_before + 2.0 * avg_atr
            normal_sl    = close_before - 1.0 * avg_atr
            day_high     = float(df["High"].iloc[pos])
            day_low      = float(df["Low"].iloc[pos])
            
            tp_breached = day_high > normal_tp
            sl_breached = day_low  < normal_sl
            
            results.append({
                "date": d,
                "event_range": round(event_range, 4),
                "avg_atr": round(avg_atr, 4),
                "multiplier": round(multiplier, 2),
                "tp_breached": tp_breached,
                "sl_breached": sl_breached,
            })
        except Exception as e:
            continue
    
    if not results:
        return None
    
    df_r = pd.DataFrame(results)
    return {
        "event": label,
        "n_events": len(df_r),
        "avg_range_multiplier": round(df_r["multiplier"].mean(), 2),
        "median_multiplier":    round(df_r["multiplier"].median(), 2),
        "p75_multiplier":       round(df_r["multiplier"].quantile(0.75), 2),
        "tp_breach_rate":       round(df_r["tp_breached"].mean() * 100, 1),
        "sl_breach_rate":       round(df_r["sl_breached"].mean() * 100, 1),
        "recommended_multiplier": round(df_r["multiplier"].quantile(0.75), 2),
    }

print("Downloading price data...")
price_data = {}
for asset in ASSETS:
    print(f"  {asset}...")
    price_data[asset] = get_price_data(asset)

print("\n" + "="*60)
print("EVENT IMPACT ANALYSIS")
print("="*60)

all_results = []
for event_type, dates in EVENTS.items():
    print(f"\n── {event_type} ({len(dates)} events) ──")
    for asset in ASSETS:
        df = price_data[asset]
        result = analyze_event_impact(df, dates, event_type)
        if result:
            result["asset"] = asset
            all_results.append(result)
            print(f"  {asset:10} | avg multiplier: {result['avg_range_multiplier']}x "
                  f"| p75: {result['p75_multiplier']}x "
                  f"| TP breach: {result['tp_breach_rate']}% "
                  f"| SL breach: {result['sl_breach_rate']}%")

print("\n" + "="*60)
print("RECOMMENDED ATR MULTIPLIERS FOR TRACK 5")
print("="*60)
summary = pd.DataFrame(all_results)
if not summary.empty:
    by_event = summary.groupby("event")["recommended_multiplier"].mean().round(2)
    for event, mult in by_event.items():
        print(f"  {event:6}: widen stops by {mult}x on event day")
    
    print("\n  Conservative recommendation (use p75, not mean):")
    for event, mult in by_event.items():
        kelly_reduction = round(1 / mult, 2)
        print(f"  {event:6}: ATR multiplier={mult}x | Kelly reduction={kelly_reduction}x")

print("\nSave this output — these are your Track 5 numbers.")
