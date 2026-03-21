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
    try:
        from data.universe import TICKERS
        from core.signal_service import generate_signal
        cache = {}
        for t in TICKERS:
            sym = t["symbol"]
            try:
                sig = generate_signal(sym, include_reasoning=False)
                if sig:
                    cache[sym] = sig
                    print(f"✓ {sym}: {sig['direction']}")
            except Exception as e:
                print(f"✗ {sym}: {e}")
            time.sleep(0.3)
        Path("data/signals_cache.json").write_text(json.dumps(cache, indent=2))
        print(f"Cache rebuilt: {len(cache)} signals")
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
        cache = json.loads(Path("data/signals_cache.json").read_text())
        from data.universe import TICKERS
        return {
            "cached": len(cache),
            "total": len(TICKERS),
            "coverage": f"{len(cache)}/{len(TICKERS)}",
            "btc_price": cache.get("BTC-USD", {}).get("current_price"),
            "btc_signal": cache.get("BTC-USD", {}).get("direction"),
        }
    except Exception as e:
        return {"error": str(e)}
