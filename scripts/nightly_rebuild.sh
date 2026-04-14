#!/bin/bash
cd ~/Desktop/quantsignal
source /opt/anaconda3/etc/profile.d/conda.sh
conda activate python_course

echo "[$(date)] Starting nightly cache rebuild..."
python3 -c "
import json, warnings
warnings.filterwarnings('ignore')
from pathlib import Path
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from app.domain.signal.service import generate_signal
from app.domain.core.energy_detector import compute_market_energy
from app.domain.data.market import fetch_ohlcv
from app.domain.data.universe import TICKERS

cache_path = Path.home() / 'Desktop/quantsignal/data/signals_cache.json'
cache, failed = {}, []

def process(t):
    sym = t['symbol']
    try:
        sig = generate_signal(sym, include_reasoning=False)
        if not sig: return sym, None
        sig['generated_at'] = datetime.now(timezone.utc).isoformat()
        if not sig.get('energy'):
            df = fetch_ohlcv(sym, period='2y')
            if df is not None:
                sig['energy'] = compute_market_energy(df)
        return sym, sig
    except:
        return sym, None

with ThreadPoolExecutor(max_workers=8) as pool:
    for sym, sig in pool.map(lambda t: process(t), TICKERS):
        if sig: cache[sym] = sig
        else: failed.append(sym)

cache_path.write_text(json.dumps(cache, indent=2))
print(f'Done: {len(cache)}/186 | failed: {len(failed)}')
"

echo "[$(date)] Pushing to Railway..."
~/Desktop/quantsignal/scripts/push_cache_to_railway.sh
echo "[$(date)] Complete"
