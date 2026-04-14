"""
Pre-compute all 86 signals + LLM reasoning locally,
saving to a JSON cache file. Railway will serve this
instead of hitting yfinance, ensuring 0ms latency and 
bypassing the Yahoo Finance IP block for the V1 launch.
"""
import json
import time
from pathlib import Path
from core.signal_service import generate_signal
from data.universe import TICKERS

CACHE_FILE = Path("data/signals_cache.json")

def build_cache():
    print(f"Building cache for {len(TICKERS)} tickers...")
    cache = {}
    for i, t in enumerate(TICKERS):
        sym = t["symbol"]
        print(f"[{i+1}/{len(TICKERS)}] Generating {sym}...")
        try:
            # We enforce includes_reasoning=True so caching gets Groq outputs
            sig = generate_signal(sym, include_reasoning=True)
            if sig:
                cache[sym] = sig
        except Exception as e:
            print(f"Failed {sym}: {e}")
            
        time.sleep(0.5)  # Politeness delay for local IP

    print(f"Finished. Saved {len(cache)} signals.")
    CACHE_FILE.write_text(json.dumps(cache, indent=2))

if __name__ == "__main__":
    build_cache()
