"""
seed_news_backtest.py
Run on weekdays (markets open) to seed news backtest database.
Usage: python3 scripts/seed_news_backtest.py
"""
import sys, json, warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, ".")

from app.domain.data.news import get_news
from app.domain.data.news_backtest import run_news_backtest, get_backtest_summary

SEED_SYMBOLS = [
    "BTC-USD", "ETH-USD", "SOL-USD",
    "GC=F", "CL=F", "EURUSD=X",
    "SPY", "QQQ", "AAPL", "NVDA",
    "RELIANCE.NS", "TCS.NS", "INFY.NS",
]

if __name__ == "__main__":
    for symbol in SEED_SYMBOLS:
        print(f"\nProcessing {symbol}...")
        try:
            news = get_news(symbol, limit=20)
            run_news_backtest(symbol, news)
        except Exception as e:
            print(f"  ERROR: {e}")

    print("\n=== BACKTEST SUMMARY ===")
    print(json.dumps(get_backtest_summary(), indent=2))
