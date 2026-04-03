"""
reasoning/worker.py
Background worker — fills reasoning into cache after signal is generated.
Does NOT block the signal pipeline.
"""

import json
import asyncio
import logging
from app.domain.reasoning.service import get_reasoning

logger = logging.getLogger(__name__)


def _update_redis(symbol: str, reasoning: str, status: str) -> None:
    try:
        from app.infrastructure.cache.cache import get_cached, set_cached, _get_redis
        from app.infrastructure.queue.reasoning_queue import _status_key
        # Update signal cache
        existing = get_cached(f"signal:{symbol}") or {}
        existing["reasoning"] = reasoning
        existing["reasoning_status"] = status
        set_cached(f"signal:{symbol}", existing, ttl=3600)
        # Write to status key so reasoning endpoint can read it
        r = _get_redis()
        if r:
            r.set(_status_key(symbol), json.dumps({
                "status": status,
                "reasoning": reasoning,
            }))
    except Exception as e:
        logger.warning(f"[reasoning_worker] _update_redis failed: {e}")


async def fill_reasoning_async(symbol: str, signal: dict) -> None:
    """
    Called as a BackgroundTask after signal is returned to client.
    Generates reasoning and writes it to Redis.
    """
    logger.info(f"[reasoning_worker] Starting for {symbol}")

    # --- Idempotency guard via Redis ---
    try:
        from app.infrastructure.cache.cache import _get_redis
        from app.infrastructure.queue.reasoning_queue import _status_key
        r = _get_redis()
        if r:
            raw = r.get(_status_key(symbol))
            if raw:
                state = json.loads(raw)
                if state.get("status") == "complete" and state.get("reasoning"):
                    logger.info(f"[reasoning_worker] {symbol} already complete — skipping")
                    return
    except Exception:
        pass

    # --- Build args ---
    news = signal.get("news", [])
    headlines = [n.get("title", "") for n in news]
    if not headlines:
        try:
            from app.domain.data.news import get_news
            items = get_news(symbol, limit=5)
            headlines = [i.title for i in items]
        except Exception:
            pass

    kwargs = dict(
        ticker=symbol,
        name=signal.get("name", symbol),
        direction=signal.get("direction", "HOLD"),
        probability=signal.get("probability", 0.5),
        confluence_bulls=int(str(signal.get("confluence_score", "0/9")).split("/")[0]),
        top_features=signal.get("top_features", []),
        news_headlines=headlines,
        current_price=signal.get("current_price", 0),
        take_profit=signal.get("take_profit", 0),
        stop_loss=signal.get("stop_loss", 0),
        atr=signal.get("atr", 0),
        model_agreement=signal.get("model_agreement", 0),
    )

    # --- Retry loop (2 attempts) ---
    reasoning = None
    for attempt in range(1, 3):
        try:
            reasoning = await asyncio.get_event_loop().run_in_executor(
                None, lambda: get_reasoning(**kwargs)
            )
            if reasoning:
                break
        except Exception as e:
            logger.warning(f"[reasoning_worker] {symbol} attempt {attempt} failed: {e}")

    # --- Write result ---
    if reasoning:
        _update_redis(symbol, reasoning, "complete")
        logger.info(f"[reasoning_worker] {symbol} complete")
    else:
        _update_redis(symbol, "", "failed")
        logger.error(f"[reasoning_worker] {symbol} failed after 2 attempts")
