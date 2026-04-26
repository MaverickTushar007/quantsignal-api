"""
Run this from your quantsignal-api root directory.
Wires circuit_breaker_v2 into:
  1. service.py — gate at top of generate_signal()
  2. cron.py    — evaluate_and_update_outcomes() on every cron run
  3. routes.py  — /system/circuit-breaker status endpoint
"""
import os, shutil

# ── Step 1: Copy circuit_breaker_v2.py into app ───────────────────────────
src = "circuit_breaker_v2.py"
dst = "app/domain/core/circuit_breaker_v2.py"
shutil.copy(src, dst)
print(f"✓ Copied {src} → {dst}")

# ── Step 2: Wire into service.py ──────────────────────────────────────────
path = "app/domain/signal/service.py"
src_txt = open(path).read()

# Add import
if "circuit_breaker_v2" not in src_txt:
    src_txt = src_txt.replace(
        "from app.domain.signal.confluence_v2 import build_confluence_v2, enforce_consistency_v2",
        "from app.domain.signal.confluence_v2 import build_confluence_v2, enforce_consistency_v2\n"
        "from app.domain.core.circuit_breaker_v2 import CircuitBreaker, check_daily_loss_limit",
    )
    print("✓ Added circuit breaker import to service.py")

# Add gate at top of generate_signal() — right after the function def and meta fetch
gate_code = """
    # ── Circuit breaker gate (QuantLive pattern) ──────────────────────────
    # Check 1: Consecutive loss circuit breaker
    cb_blocked, cb_reason = CircuitBreaker.check(symbol)
    if cb_blocked:
        log.warning(f"[generate_signal] {symbol} blocked by circuit breaker: {cb_reason}")
        return None

    # Check 2: Daily loss limit
    daily_breached, daily_pnl = check_daily_loss_limit()
    if daily_breached:
        log.warning(f"[generate_signal] {symbol} blocked: daily loss limit breached ({daily_pnl:.2f}%)")
        return None

"""

# Insert after the function signature line that fetches meta
insert_after = "    meta = TICKER_MAP.get(symbol)\n    if not meta:\n        log.warning(f\"[generate_signal] {symbol} not in TICKER_MAP\")\n        return None"

if "Circuit breaker gate" not in src_txt:
    if insert_after in src_txt:
        src_txt = src_txt.replace(insert_after, insert_after + gate_code)
        print("✓ Added circuit breaker gate to generate_signal()")
    else:
        print("⚠ Could not find insertion point in service.py — add gate manually")

open(path, "w").write(src_txt)

# ── Step 3: Wire evaluate_and_update_outcomes into cron.py ────────────────
path = "app/api/routes/cron.py"
if os.path.exists(path):
    src_txt = open(path).read()
    if "evaluate_and_update_outcomes" not in src_txt:
        # Add import
        src_txt = src_txt.replace(
            "from fastapi import APIRouter",
            "from fastapi import APIRouter\nfrom app.domain.core.circuit_breaker_v2 import evaluate_and_update_outcomes",
        )
        # Wire into existing flush/refresh endpoint
        src_txt = src_txt.replace(
            "@router.post(\"/cron/refresh\"",
            """@router.post("/cron/outcome-check")
def cron_outcome_check():
    \"\"\"Evaluate open signals against current prices. Run every 5-15 minutes.\"\"\"
    try:
        results = evaluate_and_update_outcomes()
        from app.domain.core.circuit_breaker_v2 import CircuitBreaker
        return {"status": "ok", **results, "circuit_breaker": CircuitBreaker.get_status()}
    except Exception as e:
        return {"status": "error", "detail": str(e)}


@router.post("/cron/refresh\"""",
        )
        open(path, "w").write(src_txt)
        print("✓ Added /cron/outcome-check endpoint to cron.py")
    else:
        print("⚠ cron.py already has evaluate_and_update_outcomes")
else:
    print(f"⚠ {path} not found — add outcome check endpoint manually")

# ── Step 4: Wire circuit breaker status into system routes ────────────────
# Find the circuit-breaker endpoint
path = "app/api/routes/routes.py"
if os.path.exists(path):
    src_txt = open(path).read()
    if "circuit_breaker_v2" not in src_txt and "circuit-breaker" in src_txt:
        src_txt = src_txt.replace(
            "@router.get(\"/system/circuit-breaker\")",
            """@router.get("/system/circuit-breaker")
def get_circuit_breaker_status():
    from app.domain.core.circuit_breaker_v2 import CircuitBreaker, evaluate_and_update_outcomes
    return CircuitBreaker.get_status()

@router.post("/system/circuit-breaker/reset")
def reset_circuit_breaker():
    from app.domain.core.circuit_breaker_v2 import CircuitBreaker
    CircuitBreaker._reset()
    return {"status": "reset", **CircuitBreaker.get_status()}

# Old endpoint kept for compatibility — redirects
@router.get("/system/circuit-breaker-old\"""",
        )
        open(path, "w").write(src_txt)
        print("✓ Wired circuit breaker status endpoint in routes.py")

print("\n✅ All patches applied. Run:")
print("   python3 -c \"from app.domain.core.circuit_breaker_v2 import CircuitBreaker; print(CircuitBreaker.get_status())\"")
print("   to verify, then git add app/ && git commit -m 'feat: circuit breaker v2 (QuantLive pattern)'")
