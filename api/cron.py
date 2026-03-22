"""
api/cron.py
Cache refresh endpoint — called by Railway cron or external scheduler.
Rebuilds all 118 signals and clears Redis cache.
"""
from fastapi import APIRouter, Header, HTTPException
import os, json, time, threading
from pathlib import Path

router = APIRouter()
CRON_SECRET = os.getenv("CRON_SECRET", "quantsignal_cron_2026")

def _rebuild():
    from api.alerts import fire_signal_alerts
    import json, time, threading
    from pathlib import Path
    from concurrent.futures import ThreadPoolExecutor, as_completed

    # Load old cache for alert comparison
    try:
        old_cache = json.loads(Path("data/signals_cache.json").read_text())
    except Exception:
        old_cache = {}

    try:
        from data.universe import TICKERS
        from core.signal_service import generate_signal

        # Split into 4 parallel worker groups
        GROUPS = {
            "CRYPTO":  [t for t in TICKERS if t["type"] == "CRYPTO"],
            "INDIA":   [t for t in TICKERS if t["type"] == "IN_STOCK"],
            "US":      [t for t in TICKERS if t["type"] in ("STOCK", "ETF")],
            "MACRO":   [t for t in TICKERS if t["type"] in ("INDEX", "FOREX", "COMMODITY")],
        }

        cache = {}
        cache_lock = threading.Lock()
        start_time = time.time()

        def process_group(group_name, tickers):
            results = {}
            for t in tickers:
                sym = t["symbol"]
                try:
                    sig = generate_signal(sym, include_reasoning=False)
                    if sig:
                        results[sym] = sig
                        print(f"[{group_name}] ✓ {sym}: {sig['direction']}")
                    else:
                        print(f"[{group_name}] ✗ {sym}: no data")
                except Exception as e:
                    print(f"[{group_name}] ✗ {sym}: {e}")
                time.sleep(0.2)
            return group_name, results

        # Run all 4 groups in parallel
        print(f"Starting parallel rebuild — 4 workers for {len(TICKERS)} signals...")
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {
                executor.submit(process_group, name, tickers): name
                for name, tickers in GROUPS.items()
            }
            for future in as_completed(futures):
                group_name, results = future.result()
                with cache_lock:
                    cache.update(results)
                print(f"[{group_name}] done — {len(results)} signals")

        elapsed = round(time.time() - start_time, 1)
        Path("data/signals_cache.json").write_text(json.dumps(cache, indent=2))
        print(f"Cache rebuilt: {len(cache)}/{len(TICKERS)} signals in {elapsed}s")

        # Fire signal alerts for direction changes
        try:
            fired = fire_signal_alerts(cache, old_cache)
            print(f"Signal alerts fired: {fired}")
        except Exception as e:
            print(f"Alert firing error: {e}")

        # Clear Redis
        try:
            from core.cache import _get_redis
            r = _get_redis()
            if r:
                for k in r.keys("*"):
                    r.delete(k)
                print("Redis cleared")
        except Exception as e:
            print(f"Redis clear skipped: {e}")

    except Exception as e:
        print(f"Cache rebuild failed: {e}")

@router.post("/cron/refresh", tags=["cron"])
def refresh_cache(x_cron_secret: str = Header(None, alias="X-Cron-Secret")):
    if x_cron_secret != CRON_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")
    # Run in background so request returns immediately
    thread = threading.Thread(target=_rebuild, daemon=True)
    thread.start()
    return {"status": "started", "message": "Cache rebuild running in background"}

@router.get("/cron/status", tags=["cron"])
def cache_status():
    try:
        cache_path = Path("data/signals_cache.json")
        cache = json.loads(cache_path.read_text())
        from data.universe import TICKERS
        import os
        mtime = os.path.getmtime(cache_path)
        from datetime import datetime, timezone
        last_rebuilt = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
        # Count by type
        types = {}
        for sig in cache.values():
            t = sig.get("type", "UNKNOWN")
            types[t] = types.get(t, 0) + 1
        return {
            "cached": len(cache),
            "total": len(TICKERS),
            "coverage": f"{len(cache)}/{len(TICKERS)}",
            "last_rebuilt": last_rebuilt,
            "by_type": types,
            "btc_price": cache.get("BTC-USD", {}).get("current_price"),
            "btc_signal": cache.get("BTC-USD", {}).get("direction"),
            "nifty_signal": cache.get("^NSEI", {}).get("direction"),
        }
    except Exception as e:
        return {"error": str(e)}
