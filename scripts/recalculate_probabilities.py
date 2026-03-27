"""
Recalculate all stored probabilities using latest calibration params + regime multipliers.
Run after fit_calibration.py.
Usage: python3 scripts/recalculate_probabilities.py
"""
import os, sys, math, psycopg2, json
sys.path.insert(0, ".")
from app.domain.regime.detector import regime_multiplier

def sigmoid(x):
    return 1.0 / (1.0 + math.exp(-x))

DATABASE_URL = os.environ["DATABASE_URL"]
con = psycopg2.connect(DATABASE_URL)
cur = con.cursor()

# Load latest calibration params
cur.execute("SELECT params FROM calibration_params ORDER BY created_at DESC LIMIT 1")
row = cur.fetchone()
if not row:
    print("❌ No calibration params found. Run fit_calibration.py first.")
    sys.exit(1)

params = row[0]
coef, intercept = float(params["coef"]), float(params["intercept"])
print(f"Using coef={coef:.4f}, intercept={intercept:.4f}")

# Fetch all signals with raw_probability
cur.execute("""
    SELECT id, regime, direction, raw_probability
    FROM signal_history
    WHERE raw_probability IS NOT NULL
""")
rows = cur.fetchall()
print(f"Recalculating {len(rows)} signals...")

updated = 0
for id_, regime, direction, raw_prob in rows:
    if not regime or not direction or raw_prob is None:
        continue
    calibrated = sigmoid(coef * float(raw_prob) + intercept)
    multiplier = regime_multiplier(regime, direction)
    adj = round(min(calibrated * multiplier, 1.0), 4)
    cur.execute("""
        UPDATE signal_history
        SET probability=%s, regime_multiplier=%s
        WHERE id=%s
    """, (adj, multiplier, id_))
    updated += 1

con.commit()
con.close()
print(f"✅ Done. {updated} signals recalculated.")
