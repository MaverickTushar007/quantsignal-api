"""
scheduler.py
Weekly auto-retrain scheduler using APScheduler.
Runs every Sunday 00:00 IST (18:30 UTC Saturday).
Retrains all models in the ticker universe sequentially.
"""
import logging
import os
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)

def retrain_all_models():
    """Retrain every ticker in the universe. Called by scheduler."""
    from app.domain.data.universe import TICKER_MAP
    from app.domain.ml.ensemble import train

    tickers = list(TICKER_MAP.keys())
    logger.info(f"[scheduler] Starting weekly retrain — {len(tickers)} tickers")
    start = datetime.now(timezone.utc)

    success, failed = 0, []
    for ticker in tickers:
        try:
            from app.domain.data.market import fetch_ohlcv
            df = fetch_ohlcv(ticker, period="2y")
            if df is None or len(df) < 100:
                failed.append(ticker)
                continue
            result = train(ticker, df)
            if result:
                success += 1
                logger.info(f"[scheduler] ✅ {ticker} retrained")
            else:
                failed.append(ticker)
                logger.warning(f"[scheduler] ⚠️  {ticker} returned None")
        except Exception as e:
            failed.append(ticker)
            logger.error(f"[scheduler] ❌ {ticker} failed: {e}")

    elapsed = (datetime.now(timezone.utc) - start).seconds
    logger.info(
        f"[scheduler] Retrain complete — {success}/{len(tickers)} OK, "
        f"{len(failed)} failed, took {elapsed}s"
    )
    if failed:
        logger.warning(f"[scheduler] Failed tickers: {failed}")


def start_scheduler() -> BackgroundScheduler:
    """Start the APScheduler background scheduler. Call once at app startup."""
    scheduler = BackgroundScheduler(timezone="UTC")

    # Weekly retrain: Sunday 18:30 UTC = Monday 00:00 IST
    scheduler.add_job(
        retrain_all_models,
        trigger=CronTrigger(day_of_week="sun", hour=18, minute=30),
        id="weekly_retrain",
        name="Weekly model retrain",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=3600,  # allow up to 1hr late start
    )

    # Also retrain on startup if models dir is empty or stale (>7 days)
    try:
        from pathlib import Path
        from app.core.config import BASE_DIR
        models_dir = BASE_DIR / "ml/models"
        if models_dir.exists():
            pkls = list(models_dir.glob("*.pkl"))
            if not pkls:
                logger.info("[scheduler] No models found — triggering immediate retrain")
                scheduler.add_job(
                    retrain_all_models,
                    id="startup_retrain",
                    name="Startup retrain",
                    replace_existing=True,
                    max_instances=1,
                )
            else:
                import time
                oldest = min(p.stat().st_mtime for p in pkls)
                age_days = (time.time() - oldest) / 86400
                if age_days > 7:
                    logger.info(f"[scheduler] Models are {age_days:.1f} days old — triggering retrain")
                    scheduler.add_job(
                        retrain_all_models,
                        id="stale_retrain",
                        name="Stale model retrain",
                        replace_existing=True,
                        max_instances=1,
                    )
    except Exception as e:
        logger.warning(f"[scheduler] Startup check failed: {e}")

    scheduler.start()
    logger.info("[scheduler] APScheduler started — weekly retrain scheduled (Sun 18:30 UTC)")
    return scheduler
