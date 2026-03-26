"""
infrastructure/queue/poller.py
Async poller — runs on startup, drains the reasoning queue continuously.
Replaces FastAPI BackgroundTasks for reasoning jobs.
"""
import asyncio
import logging
from app.infrastructure.queue.reasoning_queue import (
    dequeue_reasoning_job,
    mark_reasoning_complete,
    mark_reasoning_failed,
)
from app.domain.reasoning.worker import fill_reasoning_async

logger = logging.getLogger(__name__)
_poller_running = False


async def _poll_loop():
    logger.info("[poller] Reasoning queue poller started")
    while True:
        try:
            job = dequeue_reasoning_job()
            if job:
                symbol = job["symbol"]
                signal = job["signal"]
                logger.info(f"[poller] Processing job for {symbol}")
                try:
                    await fill_reasoning_async(symbol, signal)
                    mark_reasoning_complete(symbol)
                except Exception as e:
                    logger.error(f"[poller] Reasoning failed for {symbol}: {e}")
                    mark_reasoning_failed(symbol)
            else:
                await asyncio.sleep(2)
        except Exception as e:
            logger.error(f"[poller] Unexpected error: {e}")
            await asyncio.sleep(2)


def start_poller():
    """Call once on app startup to launch the poller as a background task."""
    global _poller_running
    if _poller_running:
        return
    _poller_running = True
    asyncio.create_task(_poll_loop())
    logger.info("[poller] Reasoning queue poller scheduled")
