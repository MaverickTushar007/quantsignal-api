#!/bin/bash
cd ~/Desktop/quantsignal
echo "$(date): Refreshing signal cache..."

/opt/anaconda3/envs/quant-signals/bin/python -c "
import json
from pathlib import Path
from data.universe import TICKERS
import data.market as dm
from data.market import fetch_coingecko_ohlcv
import yfinance as yf

def forced_cg(ticker, period='2y'):
    if ticker in dm.COINGECKO_ID_MAP:
        return fetch_coingecko_ohlcv(ticker, days=365)
    t = yf.Ticker(ticker)
    df = t.history(period=period, auto_adjust=True)
    if df is not None:
        df.index = df.index.tz_localize(None) if df.index.tzinfo else df.index
    return df

dm.fetch_ohlcv = forced_cg
from core.signal_service import generate_signal
from core.cache import _get_redis

cache = {}
for t in TICKERS:
    sym = t['symbol']
    try:
        sig = generate_signal(sym, include_reasoning=False)
        if sig:
            cache[sym] = sig
    except Exception as e:
        print(f'{sym}: FAILED - {e}')

Path('data/signals_cache.json').write_text(json.dumps(cache))
print(f'Cached {len(cache)} signals, BTC=\${cache[\"BTC-USD\"][\"current_price\"]:,.2f}')

# Clear Redis so Railway picks up new cache
r = _get_redis()
for k in r.keys('*'): r.delete(k)
print('Redis cleared')
"

git add data/signals_cache.json
git commit -m "Daily cache refresh — $(date '+%Y-%m-%d %H:%M')"
git push origin master
echo "Done."
