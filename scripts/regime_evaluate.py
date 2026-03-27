import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import os
os.environ.setdefault("DATABASE_URL",
    "postgresql://postgres:IelBNgTSlBIUrCSFYpmXTkrGIfzPdfVJ@gondola.proxy.rlwy.net:11308/railway")

from app.infrastructure.db.signal_history import get_open_signals, update_outcome
from app.domain.regime.detector import detect_regime, regime_multiplier
from app.domain.performance.evaluator import _get_price

def run():
    signals = get_open_signals()
    print(f"open signals: {len(signals)}")

    for s in signals:
        sym = s["symbol"]
        price = _get_price(sym)
        regime_data = detect_regime(sym)
        regime = regime_data.get("regime", "unknown")
        mult = regime_multiplier(regime, s["direction"])
        adj_prob = round(min((s.get("probability") or 0.5) * mult, 1.0), 3)

        print(f"\n{sym} {s['direction']}")
        print(f"  regime={regime} multiplier={mult} adj_prob={adj_prob}")
        print(f"  price={price} tp={s['take_profit']} sl={s['stop_loss']}")

        if not price:
            print("  → skipped (no price)")
            continue

        outcome = None
        if s["direction"] == "BUY":
            if price >= s["take_profit"]: outcome = "win"
            elif price <= s["stop_loss"]: outcome = "loss"
        elif s["direction"] == "SELL":
            if price <= s["take_profit"]: outcome = "win"
            elif price >= s["stop_loss"]: outcome = "loss"

        if outcome:
            update_outcome(s["id"], outcome, price)
            print(f"  → {outcome} @ {price}")
        else:
            print(f"  → still open (price between TP/SL)")

if __name__ == "__main__":
    run()
