"""
main.py
FastAPI application entry point.
Run with: python -m uvicorn main:app --reload
"""

from fastapi import FastAPI
from contextlib import asynccontextmanager
from fastapi.middleware.cors import CORSMiddleware
import os
from fastapi.middleware.gzip import GZipMiddleware
from app.api.middleware.logging_middleware import LoggingMiddleware
from app.api.middleware.protection import protection_middleware
from app.core.config import settings
from app.api.routes.routes import router
from app.api.routes.market_context import router as market_context_router
from app.api.routes.signal_stream import router as signal_stream_router
from app.api.routes.system import router as system_router
from app.api.routes.metrics import router as metrics_router
from app.api.routes.performance import router as performance_router
from app.api.routes.chat import router as chat_router
from app.api.routes.sentiment import router as sentiment_router
from app.api.routes.liquidity import router as liquidity_router
from app.api.routes.replay import router as replay_router
from app.api.routes.ai_explain import router as ai_explain_router
from app.api.routes.guardian import router as guardian_router
from app.api.routes.portfolio import router as portfolio_router
from app.api.routes.cron import router as cron_router
from app.api.routes.agents import router as agents_router
from app.api.routes.montecarlo import router as mc_router
from app.api.routes.alerts import router as alerts_router
from app.api.routes.mcp import router as mcp_router
from app.api.routes.history import router as history_router
try:
    from app.api.routes.calendar import router as calendar_router
    _calendar_ok = True
except Exception as e:
    print(f"Calendar router import failed: {e}")
    _calendar_ok = False
from app.api.routes.ws import router as ws_router
from app.api.routes.preferences import router as prefs_router
from app.api.routes.weekly_report import router as weekly_report_router
from app.api.routes.admin import router as admin_router
from app.api.routes.billing import router as billing_router
from app.api.routes.feedback import router as feedback_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio
    tasks = []
    try:
        from app.infrastructure.queue.poller import start_poller
        tasks.append(asyncio.create_task(start_poller()))
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Poller failed to start: {e}")

    try:
        from app.infrastructure.scheduler.refresh_scheduler import run_refresh_scheduler
        tasks.append(asyncio.create_task(run_refresh_scheduler()))
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Refresh scheduler failed to start: {e}")

    # DB migrations
    try:
        from app.infrastructure.db.signal_history import ensure_calibration_table
        ensure_calibration_table()
    except Exception as _dbe:
        import logging
        logging.getLogger(__name__).warning(f"[startup] DB migration failed: {_dbe}")
    # Auto-rebuild cache on startup (Railway filesystem is ephemeral)
    try:
        import threading
        from app.api.routes.tasks import _rebuild
        _t = threading.Thread(target=_rebuild, daemon=True)
        _t.start()
        import logging
        logging.getLogger(__name__).info("[startup] cache rebuild triggered")
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"[startup] cache rebuild failed: {e}")
    yield

    for t in tasks:
        t.cancel()

app = FastAPI(
    title="QuantSignal API",
    description="ML-powered trading signals",
    version="1.0.2",
    docs_url="/docs",
    redoc_url=None,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(","),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "x-user-id"],
)
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(LoggingMiddleware)

app.include_router(router, prefix="/api/v1")
app.include_router(market_context_router, prefix="/api/v1")
app.include_router(signal_stream_router, prefix="/api/v1")
app.include_router(system_router, prefix="/api/v1")
app.include_router(chat_router, prefix="/api/v1")
app.include_router(sentiment_router, prefix="/api/v1")
app.include_router(liquidity_router, prefix="/api/v1")
app.include_router(replay_router, prefix="/api/v1")
app.include_router(ai_explain_router, prefix="/api/v1")
app.include_router(guardian_router, prefix="/api/v1")
app.include_router(portfolio_router, prefix="/api/v1")
app.include_router(billing_router, prefix="/api/v1")
app.include_router(cron_router, prefix="/api/v1")
app.include_router(agents_router, prefix="/api/v1")
app.include_router(mc_router, prefix="/api/v1")
app.include_router(alerts_router, prefix="/api/v1")
app.include_router(mcp_router, prefix="/api/v1")
app.include_router(history_router, prefix="/api/v1")
app.include_router(metrics_router, prefix="/api/v1")
app.include_router(performance_router, prefix="/api/v1")
if _calendar_ok:
    app.include_router(calendar_router, prefix="/api/v1")
app.include_router(ws_router, prefix="/api/v1")
app.include_router(prefs_router, prefix="/api/v1")
app.include_router(weekly_report_router, prefix="/api/v1")
app.include_router(admin_router, prefix="/api/v1")
app.include_router(feedback_router, prefix="/api/v1")


@app.get("/")
async def root():
    return {"name": "QuantSignal API", "version": "1.0.0", "docs": "/docs"}
app.middleware('http')(protection_middleware)
# rebuild Sun Mar 29 15:18:59 IST 2026
