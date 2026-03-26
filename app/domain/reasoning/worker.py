"""
reasoning/worker.py
Background worker — fills reasoning into cache after signal is generated.
Does NOT block the signal pipeline.

Hardening:
- Idempotency guard (skip if already complete)
- Explicit reasoning_status field (pending / complete / failed)
- 2-attempt retry before marking failed
- Structured logging at every stage
"""

import json
import asyncio
import logging
from pathlib import Path
from app.core.config import BASE_DIR, settings
from app.domain.reasoning.service import get_reasoning

logger = logging.getLogger(__name__)

CACHE_PATH = BASE_DIR / "data/signals_cache.json"


def _read_cache() -> dict:
    if not CACHE_PATH.exists():
        return {}
    return json.loads(CACHE_PATH.read_text())


def _write_cache(cache: dict) -> None:
    CACHE_PATH.write_text(json.dumps(cache, indent=2))


def _update_redis(symbol: str, data: dict) -> None:
    try:
        from app.infrastructure.cache.cache import get_cached, set_cached
        existing = get_cached(f"signal:{symbol}")
        if existing:
            existing["reasoning"] = data["reasoning"]
            existing["reasoning_status"] = data["reasoning_status"]
            set_cached(f"signal:{symbol}", existing, ttl=3600)
    except Exception:
        pass


async def fill_reasoning_async(symbol: str, signal: dict) -> None:
    """
    Called as a BackgroundTask after signal is returned to client.
    Generates reasoning and writes it to cache.
    """
    logger.info(f"[reasoning_worker] Starting for {symbol}")

    # --- Idempotency guard ---
    cache = _read_cache()
    if cache.get(symbol, {}).get("reasoning_status") == "complete":
        logger.info(f"[reasoning_worker] {symbol} already complete — skipping")
        return

    # --- Mark as pending ---
    if symbol in cache:
        cache[symbol]["reasoning_status"] = "pending"
        _write_cache(cache)

    # --- Build args ---
    news = signal.get("news", [])
    headlines = [n.get("title", "") for n in news]

    kwargs = dict(
        ticker=symbol,
        name=signal.get("name", symbol),
        direction=signal.get("direction", "HOLD"),
        probability=signal.get("probability", 0.5),
        confluence_bulls=int(signal.get("confluence_score", "0/9").split("/")[0]),
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
            break
        except Exception as e:
            logger.warning(f"[reasoning_worker] {symbol} attempt {attempt} failed: {e}")

    # --- Write result ---
    cache = _read_cache()
    if symbol not in cache:
        logger.error(f"[reasoning_worker] {symbol} not found in cache after generation")
        return

    if reasoning:
        cache[symbol]["reasoning"] = reasoning
        cache[symbol]["reasoning_status"] = "complete"
        _write_cache(cache)
        _update_redis(symbol, cache[symbol])
        logger.info(f"[reasoning_worker] {symbol} complete")
    else:
        cache[symbol]["reasoning_status"] = "failed"
        _write_cache(cache)
        logger.error(f"[reasoning_worker] {symbol} failed after 2 attempts")
