"""
main.py
FastAPI application entry point.
Run with: python -m uvicorn main:app --reload
"""

from fastapi import FastAPI
from contextlib import asynccontextmanager
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from app.api.middleware.protection import protection_middleware
from app.core.config import settings
from app.api.routes.routes import router
from app.api.routes.metrics import router as metrics_router
from app.api.routes.performance import router as performance_router
from app.api.routes.chat import router as chat_router
from app.api.routes.sentiment import router as sentiment_router
from app.api.routes.liquidity import router as liquidity_router
from app.api.routes.replay import router as replay_router
from app.api.routes.ai_explain import router as ai_explain_router
from app.api.routes.guardian import router as guardian_router
from app.api.routes.portfolio import router as portfolio_router
from app.api.routes.payments import router as payments_router
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.infrastructure.queue.poller import start_poller
    start_poller()
    yield

app = FastAPI(
    title="QuantSignal API",
    description="ML-powered trading signals",
    version="1.0.0",
    docs_url="/docs",
    redoc_url=None,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(GZipMiddleware, minimum_size=1000)

app.include_router(router, prefix="/api/v1")
app.include_router(chat_router, prefix="/api/v1")
app.include_router(sentiment_router, prefix="/api/v1")
app.include_router(liquidity_router, prefix="/api/v1")
app.include_router(replay_router, prefix="/api/v1")
app.include_router(ai_explain_router, prefix="/api/v1")
app.include_router(guardian_router, prefix="/api/v1")
app.include_router(portfolio_router, prefix="/api/v1")
app.include_router(payments_router, prefix="/api/v1")
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


@app.get("/")
async def root():
    return {"name": "QuantSignal API", "version": "1.0.0", "docs": "/docs"}
app.middleware('http')(protection_middleware)
