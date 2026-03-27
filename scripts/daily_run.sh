#!/bin/bash
# Daily QuantSignal runner — run once per day
# crontab: 0 9 * * * /bin/bash ~/Desktop/quantsignal/scripts/daily_run.sh >> ~/Desktop/quantsignal/logs/daily.log 2>&1

cd ~/Desktop/quantsignal
echo "=== $(date) ==="

# 1. Push regime data to Railway
echo "--- Pushing regime data ---"
python3 -c "
import sys
sys.path.insert(0, '.')
import requests
from app.domain.regime.detector import detect_regime
symbols = ['ETH-USD','SOL-USD','BNB-USD','XRP-USD','AVAX-USD','DOGE-USD','BTC-USD']
for sym in symbols:
    r = detect_regime(sym)
    r['symbol'] = sym
    resp = requests.post('https://quantsignal-api-production.up.railway.app/api/v1/regime/cache', json=r, timeout=5)
    print(f'{sym}: regime={r[\"regime\"]} status={resp.status_code}')
"

# 2. Evaluate open signals
echo "--- Evaluating open signals ---"
python3 scripts/regime_evaluate.py

# 3. Reseed fresh signals
echo "--- Reseeding signals ---"
for sym in ETH-USD SOL-USD BNB-USD XRP-USD AVAX-USD DOGE-USD BTC-USD; do
  curl -s https://quantsignal-api-production.up.railway.app/api/v1/signals/$sym > /dev/null
  echo "seeded $sym"
done

# 4. Print portfolio summary
echo "--- Portfolio summary ---"
curl -s "https://quantsignal-api-production.up.railway.app/api/v1/portfolio?min_prob=0.0&min_mtf=1&compare=true" | python3 -m json.tool

echo "=== Done ==="
