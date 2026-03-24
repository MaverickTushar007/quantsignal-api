"""
Fetch Nifty 50 + S&P 500 benchmark returns and save to data/benchmark_cache.json.
Run this locally, commit the file, and it'll be available on Railway.
"""
import yfinance as yf, json
from pathlib import Path

data = json.loads(Path("data/signal_history.json").read_text())
trades = sorted(data["trades"], key=lambda t: t.get("date", ""))
start_date = trades[0]["date"]
end_date   = trades[-1]["date"]

benchmark = {}
for name, ticker in [("Nifty 50", "^NSEI"), ("S&P 500", "^GSPC")]:
    try:
        df = yf.Ticker(ticker).history(start=start_date, end=end_date)
        if df.empty:
            print(f"{name}: no data")
            continue
        base = df["Close"].iloc[0]
        ret  = (df["Close"].iloc[-1] - base) / base * 100
        curve = [
            {"date": str(d.date()), "cumulative_pnl": round((p - base) / base * 100, 3)}
            for d, p in zip(df.index, df["Close"])
        ]
        benchmark[name] = {"return": round(ret, 2), "curve": curve}
        print(f"{name}: {ret:.2f}%")
    except Exception as e:
        print(f"{name} failed: {e}")

Path("data/benchmark_cache.json").write_text(json.dumps(benchmark))
print("Saved to data/benchmark_cache.json")
