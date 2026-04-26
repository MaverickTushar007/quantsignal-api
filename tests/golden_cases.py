"""
tests/golden_cases.py
Perseus v2 — Golden test harness. W1.6.
Run: python3 tests/golden_cases.py
Every case must pass before deploy.
"""
import asyncio, json, sys
sys.path.insert(0, ".")

TICKER_CASES = [
    {"id": "T001", "symbol": "RELIANCE.NS"},
    {"id": "T002", "symbol": "HDFCBANK.NS"},
    {"id": "T003", "symbol": "NIFTY50"},
    {"id": "T004", "symbol": "BTC-USD"},
    {"id": "T005", "symbol": "TCS.NS"},
    {"id": "T006", "symbol": "INFY.NS"},
    {"id": "T007", "symbol": "ICICIBANK.NS"},
    {"id": "T008", "symbol": "AXISBANK.NS"},
    {"id": "T009", "symbol": "SBIN.NS"},
    {"id": "T010", "symbol": "WIPRO.NS"},
]

MUST_HAVE = [
    ("symbol",               lambda v: isinstance(v, str) and len(v) > 0),
    ("confidence",           lambda v: v in ("high","moderate","low","insufficient")),
    ("freshness_seconds",    lambda v: isinstance(v, (int,float)) and v >= 0),
    ("verification",         lambda v: isinstance(v, dict)),
    ("verification.score",   lambda p: 0.0 <= p["verification"]["score"] <= 1.0),
    ("verification.passed",  lambda p: isinstance(p["verification"]["passed"], bool)),
    ("verification.issues",  lambda p: isinstance(p["verification"]["issues"], list)),
    ("risk_flags",           lambda v: isinstance(v, list)),
    ("evidence",             lambda v: isinstance(v, list)),
    ("model_used",           lambda v: isinstance(v, str) and len(v) > 0),
]

MUST_NOT_HAVE = [
    ("kelly_size > 0.25",  lambda p: p.get("kelly_size") is None or float(p.get("kelly_size") or 0) <= 0.25),
    ("probability > 1",    lambda p: p.get("probability") is None or float(p.get("probability") or 0) <= 1.0),
    ("probability < 0",    lambda p: p.get("probability") is None or float(p.get("probability") or 0) >= 0.0),
    ("freshness None",     lambda p: p.get("freshness_seconds") is not None),
]

async def run_case(case):
    from app.domain.research.ticker_packet import build_ticker_packet
    symbol = case["symbol"]
    try:
        packet = await build_ticker_packet(symbol)
        p = packet.to_dict()
    except Exception as e:
        return {"id": case["id"], "symbol": symbol, "status": "ERROR", "error": str(e), "failures": []}

    failures = []

    for name, check in MUST_HAVE:
        try:
            val = p if "." in name else p.get(name.split(".")[0])
            ok  = check(p) if "." in name else check(val)
            if not ok:
                failures.append(f"MUST_HAVE failed: {name} = {repr(val)}")
        except Exception as ex:
            failures.append(f"MUST_HAVE error: {name} → {ex}")

    for name, check in MUST_NOT_HAVE:
        try:
            ok = check(p)
            if not ok:
                failures.append(f"MUST_NOT_HAVE violated: {name}")
        except Exception as ex:
            failures.append(f"MUST_NOT_HAVE error: {name} → {ex}")

    status = "PASS" if not failures else "FAIL"
    return {
        "id":       case["id"],
        "symbol":   symbol,
        "status":   status,
        "failures": failures,
        "snapshot": {
            "direction":         p.get("direction"),
            "confidence":        p.get("confidence"),
            "probability":       p.get("probability"),
            "kelly_size":        p.get("kelly_size"),
            "freshness_seconds": p.get("freshness_seconds"),
            "verification":      p.get("verification"),
            "risk_flags_count":  len(p.get("risk_flags", [])),
            "evidence_count":    len(p.get("evidence", [])),
        }
    }

async def main():
    print("\n" + "="*60)
    print("PERSEUS GOLDEN TEST HARNESS — TICKER FLOW")
    print("="*60)
    results = []
    for case in TICKER_CASES:
        print(f"\nRunning {case['id']} — {case['symbol']}...")
        r = await run_case(case)
        results.append(r)
        status_icon = "✅" if r["status"] == "PASS" else ("❌" if r["status"] == "FAIL" else "💥")
        print(f"  {status_icon} {r['status']}")
        if r.get("snapshot"):
            s = r["snapshot"]
            print(f"     direction={s['direction']} conf={s['confidence']} prob={s['probability']} kelly={s['kelly_size']}")
            print(f"     freshness={s['freshness_seconds']}s  verifier={s.get('verification',{}).get('score')} passed={s.get('verification',{}).get('passed')}")
            print(f"     risk_flags={s['risk_flags_count']} evidence={s['evidence_count']}")
        for f in r.get("failures", []):
            print(f"     ⚠  {f}")
        if r.get("error"):
            print(f"     💥 {r['error']}")

    passed = sum(1 for r in results if r["status"] == "PASS")
    failed = sum(1 for r in results if r["status"] == "FAIL")
    errors = sum(1 for r in results if r["status"] == "ERROR")

    print("\n" + "="*60)
    print(f"RESULTS: {passed} passed / {failed} failed / {errors} errors")
    print("="*60)

    with open("tests/golden_results_latest.json", "w") as f:
        json.dump(results, f, indent=2, default=str)
    print("Results saved → tests/golden_results_latest.json")

    if failed + errors > 0:
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
