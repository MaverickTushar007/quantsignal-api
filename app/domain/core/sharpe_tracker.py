"""
domain/core/sharpe_tracker.py
W4.6 — Weekly rolling Sharpe per signal class.
Run by cron every Sunday. Writes to signal_performance table.
"""
import logging, os
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)
ALERT_THRESHOLD = 0.2
LOOKBACK_DAYS   = 28

def _client():
    from supabase import create_client
    return create_client(
        os.getenv("SUPABASE_URL", ""),
        os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_ANON_KEY", ""),
    )

def compute_sharpe(returns: list) -> float:
    import numpy as np
    r = np.array(returns, dtype=float)
    if len(r) < 2:
        return 0.0
    return float(np.mean(r) / (np.std(r) + 1e-9) * np.sqrt(252))

def run_weekly_sharpe() -> dict:
    """
    Compute rolling 28-day Sharpe per direction + asset_class.
    Writes to signal_performance. Returns summary.
    """
    try:
        sb = _client()
        since = (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)).isoformat()
        res = (
            sb.table("signals")
            .select("symbol,direction,outcome,probability,asset_type,created_at")
            .gte("created_at", since)
            .not_.is_("outcome", "null")
            .execute()
        )
        rows = res.data or []
        if not rows:
            return {"status": "no_data", "rows": 0}

        # Group by direction + asset_type
        groups: dict = {}
        for r in rows:
            key = f"{r.get('direction','?')}_{r.get('asset_type','unknown')}"
            groups.setdefault(key, [])
            outcome = r.get("outcome")
            ret = 1.0 if outcome in ("win","WIN",True,1) else -1.0
            groups[key].append(ret)

        results = []
        alerts  = []
        for key, returns in groups.items():
            direction, _, asset_class = key.partition("_")
            sharpe = compute_sharpe(returns)
            win_rate = round(sum(1 for r in returns if r > 0) / len(returns), 3)
            row = {
                "period_days":  LOOKBACK_DAYS,
                "direction":    direction,
                "asset_class":  asset_class,
                "sharpe":       round(sharpe, 3),
                "win_rate":     win_rate,
                "n_signals":    len(returns),
                "computed_at":  datetime.now(timezone.utc).isoformat(),
            }
            results.append(row)
            if sharpe < ALERT_THRESHOLD:
                alerts.append(f"{key} Sharpe={sharpe:.2f} below {ALERT_THRESHOLD}")
                log.warning(f"[sharpe_tracker] ⚠ {key} Sharpe={sharpe:.2f} < {ALERT_THRESHOLD}")

        try:
            sb.table("signal_performance").insert(results).execute()
            log.info(f"[sharpe_tracker] wrote {len(results)} rows to signal_performance")
        except Exception as _se:
            log.warning(f"[sharpe_tracker] Supabase write failed: {_se}")

        return {
            "status":   "ok",
            "computed": len(results),
            "alerts":   alerts,
            "results":  results,
        }
    except Exception as e:
        log.error(f"[sharpe_tracker] failed: {e}")
        return {"status": "error", "error": str(e)}

if __name__ == "__main__":
    import json
    print(json.dumps(run_weekly_sharpe(), indent=2, default=str))
