"""
infrastructure/scheduler/refresh_scheduler.py
Phase 2 — Freshness tier scheduler.
Replaces daily-only git-based cache refresh with proper tiered refresh:
  REALTIME  → every 1 min  (prices, spreads)
  INTRADAY  → every 1 hour (news, sentiment, fear/greed)
  DAILY     → 6 AM IST     (signals, fundamentals)
Runs alongside the reasoning queue poller in lifespan.
"""
import asyncio
import logging
from datetime import datetime, timezone

log = logging.getLogger(__name__)

# IST = UTC+5:30
IST_OFFSET_HOURS = 5.5


def _now_ist_hour() -> float:
    now = datetime.now(timezone.utc)
    return (now.hour + now.minute / 60 + IST_OFFSET_HOURS) % 24


async def _refresh_realtime():
    """Every 1 min during market hours — price cache."""
    try:
        from app.domain.data.market import refresh_live_prices
        await refresh_live_prices() if asyncio.iscoroutinefunction(
            refresh_live_prices) else refresh_live_prices()
    except AttributeError:
        pass  # function may not exist yet — safe skip
    except Exception as e:
        log.warning(f"[scheduler] realtime refresh failed: {e}")


async def _refresh_intraday():
    """Every 1 hour — news cache, fear/greed."""
    try:
        from app.domain.data.news import refresh_news_cache
        await refresh_news_cache() if asyncio.iscoroutinefunction(
            refresh_news_cache) else refresh_news_cache()
    except (AttributeError, ImportError):
        pass
    except Exception as e:
        log.warning(f"[scheduler] news refresh failed: {e}")

    try:
        from app.domain.data.fear_greed import refresh_fear_greed
        await refresh_fear_greed() if asyncio.iscoroutinefunction(
            refresh_fear_greed) else refresh_fear_greed()
    except (AttributeError, ImportError):
        pass
    except Exception as e:
        log.warning(f"[scheduler] fear/greed refresh failed: {e}")


async def _refresh_daily():
    """6 AM IST — full signal rebuild."""
    try:
        from app.domain.signal.service import rebuild_all_signals
        await rebuild_all_signals() if asyncio.iscoroutinefunction(
            rebuild_all_signals) else rebuild_all_signals()
        log.info("[scheduler] daily signal rebuild complete")
    except (AttributeError, ImportError):
        log.info("[scheduler] rebuild_all_signals not available — skipping daily rebuild")
    except Exception as e:
        log.error(f"[scheduler] daily rebuild failed: {e}")


async def run_refresh_scheduler():
    """
    Main scheduler loop. Runs as an asyncio task in lifespan.
    Tracks last-run times and fires refresh functions on their cadence.
    """
    log.info("[scheduler] Freshness tier scheduler started")

    last_realtime  = 0.0
    last_intraday  = 0.0
    last_daily_day = -1

    REALTIME_INTERVAL  = 60      # seconds
    INTRADAY_INTERVAL  = 3600    # seconds
    DAILY_IST_HOUR     = 6.0     # 6:00 AM IST

    while True:
        try:
            now = asyncio.get_event_loop().time()
            ist_hour = _now_ist_hour()
            today = datetime.now(timezone.utc).day

            # Realtime — every 60 seconds
            if now - last_realtime >= REALTIME_INTERVAL:
                await _refresh_realtime()
                last_realtime = now

            # Intraday — every hour
            if now - last_intraday >= INTRADAY_INTERVAL:
                await _refresh_intraday()
                last_intraday = now

            # Daily — once per day at 6 AM IST
            if today != last_daily_day and DAILY_IST_HOUR <= ist_hour < DAILY_IST_HOUR + 0.25:
                await _refresh_daily()
                last_daily_day = today

        except asyncio.CancelledError:
            log.info("[scheduler] Freshness scheduler cancelled — shutting down")
            break
        except Exception as e:
            log.error(f"[scheduler] Unexpected error in scheduler loop: {e}")

        await asyncio.sleep(30)  # check every 30s, act on cadence
