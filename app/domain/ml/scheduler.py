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
    register_data_jobs(scheduler)
    logger.info("[scheduler] APScheduler started — weekly retrain scheduled (Sun 18:30 UTC)")
    return scheduler


# ── COT + News Backtest scheduled jobs ───────────────────────────────────────

def _reseed_cot():
    """
    Weekly COT re-seed from CFTC (runs every Monday 08:00 UTC,
    ~3h after CFTC publishes Friday's report).
    Fetches both c_disagg.txt (commodities) and FinFutWk.txt (financials).
    """
    try:
        from app.domain.data.cot import _fetch_cot_url, _parse_cot_csv, _load_cache, _save_cache
        from datetime import datetime
        cache = _load_cache()
        for label, url in [
            ("commodity", "https://www.cftc.gov/dea/newcot/c_disagg.txt"),
            ("financial", "https://www.cftc.gov/dea/newcot/FinFutWk.txt"),
        ]:
            raw = _fetch_cot_url(url)
            if raw:
                parsed = _parse_cot_csv(raw)
                for name, data in parsed.items():
                    data["fetched_at"] = datetime.utcnow().isoformat()
                    data["source"] = "CFTC"
                    data["available"] = True
                    cache[f"cot::{name}"] = data
                logger.info(f"[cot_reseed] {label}: {len(parsed)} markets updated")
        _save_cache(cache)
        logger.info("[cot_reseed] COT cache refreshed successfully")
    except Exception as e:
        logger.error(f"[cot_reseed] failed: {e}")


def _seed_news_backtest():
    """
    Daily news backtest seed — runs Mon-Fri at 16:00 UTC (after US market close).
    Fetches recent news for all tracked symbols and scores sentiment vs price outcome.
    """
    try:
        from app.domain.data.news import get_news
        from app.domain.data.news_backtest import run_news_backtest
        SYMBOLS = [
            "BTC-USD", "ETH-USD", "SOL-USD",
            "GC=F", "CL=F", "EURUSD=X",
            "SPY", "QQQ", "AAPL", "NVDA",
            "RELIANCE.NS", "TCS.NS", "INFY.NS",
        ]
        for symbol in SYMBOLS:
            try:
                news = get_news(symbol, limit=10)
                run_news_backtest(symbol, news)
            except Exception as _e:
                logger.warning(f"[news_backtest_seed] {symbol}: {_e}")
        logger.info("[news_backtest_seed] daily seed complete")
    except Exception as e:
        logger.error(f"[news_backtest_seed] failed: {e}")


def _refresh_macro_regime():
    """Daily macro regime refresh — runs at 07:00 UTC using FRED data."""
    try:
        from app.domain.data.macro_regime import get_macro_regime
        import os; os.environ.setdefault("FORCE_REGIME_REFRESH", "1")
        # Delete cache to force refresh
        from app.domain.data.macro_regime import REGIME_CACHE
        if REGIME_CACHE.exists():
            REGIME_CACHE.unlink()
        result = get_macro_regime()
        logger.info(f"[macro_regime] refreshed: {result['regime']} vix={result['details'].get('vix')}")
    except Exception as e:
        logger.error(f"[macro_regime] refresh failed: {e}")


def register_data_jobs(scheduler: "BackgroundScheduler"):
    """
    Call this from start_scheduler() to register COT + news backtest jobs.
    """
    # COT: every Monday 08:00 UTC
    scheduler.add_job(
        _reseed_cot,
        trigger="cron",
        day_of_week="mon",
        hour=8, minute=0,
        id="cot_weekly_reseed",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    # News backtest: Mon-Fri 16:00 UTC
    scheduler.add_job(
        _seed_news_backtest,
        trigger="cron",
        day_of_week="mon-fri",
        hour=16, minute=0,
        id="news_backtest_daily_seed",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    scheduler.add_job(
        _refresh_macro_regime,
        trigger="cron",
        hour=7, minute=0,
        id="macro_regime_daily",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    logger.info("[scheduler] COT weekly + news backtest daily + macro regime jobs registered")
