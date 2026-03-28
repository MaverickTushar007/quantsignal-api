#!/bin/bash
cd ~/Desktop/quantsignal
source /opt/anaconda3/etc/profile.d/conda.sh
conda activate python_course

echo "[$(date)] Starting nightly cache rebuild..."

python3 -c "
from app.domain.signal.service import generate_signal
from app.domain.core.energy_detector import compute_market_energy
from app.domain.data.market import fetch_ohlcv
import json
from app.domain.data.universe import TICKERS
from pathlib import Path

cache = {}
failed = []
for t in TICKERS:
    sym = t['symbol']
    try:
        sig = generate_signal(sym, include_reasoning=False)
        if sig:
            # Add energy if missing
            if not sig.get('energy'):
                try:
                    df = fetch_ohlcv(sym, period='2y')
                    if df is not None:
                        sig['energy'] = compute_market_energy(df)
                except Exception:
                    pass
            cache[sym] = sig
    except Exception as e:
        failed.append(sym)

Path('data/signals_cache.json').write_text(json.dumps(cache, indent=2))
print(f'Done: {len(cache)} signals, {len(failed)} failed')
print(f'With energy: {sum(1 for s in cache.values() if s.get(\"energy\"))}')
"

echo "[$(date)] Pushing to Railway..."
~/Desktop/quantsignal/scripts/push_cache_to_railway.sh
echo "[$(date)] Nightly rebuild complete"
