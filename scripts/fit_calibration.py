"""
Refit Platt scaling calibration from closed signals in DB.
Run this whenever you have enough new closed signals (aim for 200+).
Usage: python3 scripts/fit_calibration.py
"""
import os, sys, math, psycopg2, json
sys.path.insert(0, ".")

DATABASE_URL = os.environ["DATABASE_URL"]

def sigmoid(x):
    return 1.0 / (1.0 + math.exp(-x))

def fit_platt(probs, labels, lr=0.01, epochs=1000):
    coef, intercept = 1.0, 0.0
    n = len(probs)
    for _ in range(epochs):
        d_coef = d_intercept = 0.0
        for p, y in zip(probs, labels):
            pred = sigmoid(coef * p + intercept)
            err = pred - y
            d_coef      += err * p
            d_intercept += err
        coef      -= lr * d_coef / n
        intercept -= lr * d_intercept / n
    return coef, intercept

con = psycopg2.connect(DATABASE_URL)
cur = con.cursor()

cur.execute("""
    SELECT raw_probability, outcome
    FROM signal_history
    WHERE raw_probability IS NOT NULL
      AND outcome IN ('win', 'loss')
""")
rows = cur.fetchall()
print(f"Fitting on {len(rows)} closed signals...")

if len(rows) < 30:
    print("❌ Need at least 30 closed signals to refit. Exiting.")
    sys.exit(1)

probs  = [float(r[0]) for r in rows]
labels = [1 if r[1] == 'win' else 0 for r in rows]
win_rate = sum(labels) / len(labels)

coef, intercept = fit_platt(probs, labels)
print(f"coef:      {coef:.4f}")
print(f"intercept: {intercept:.4f}")
print(f"win_rate:  {win_rate:.3f}")
print(f"n_samples: {len(rows)}")

cur.execute("""
    INSERT INTO calibration_params (params, n_samples, win_rate)
    VALUES (%s, %s, %s)
""", (json.dumps({"coef": coef, "intercept": intercept}), len(rows), win_rate))
con.commit()
con.close()
print("✅ Calibration params saved to DB.")
