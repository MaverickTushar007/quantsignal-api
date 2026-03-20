"""
main.py
FastAPI application entry point.
Run with: python -m uvicorn main:app --reload
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from core.config import settings
from api.routes import router
from api.chat import router as chat_router
from api.sentiment import router as sentiment_router
from api.liquidity import router as liquidity_router
from api.replay import router as replay_router
try:
    from api.calendar import router as calendar_router
    _calendar_ok = True
except Exception as e:
    print(f"Calendar router import failed: {e}")
    _calendar_ok = False
from api.ws import router as ws_router

app = FastAPI(

    title="QuantSignal API",
    description="ML-powered trading signals — XGBoost + LightGBM + LLM reasoning",
    version="1.0.0",
    docs_url="/docs",
    redoc_url=None,
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
if _calendar_ok:
    app.include_router(calendar_router, prefix="/api/v1")
app.include_router(ws_router, prefix="/api/v1")


@app.get("/")
async def root():
    return {"name": "QuantSignal API", "version": "1.0.0", "docs": "/docs"}
