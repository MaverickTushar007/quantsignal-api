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
app.include_router(ws_router, prefix="/api/v1")


@app.get("/")
async def root():
    return {"name": "QuantSignal API", "version": "1.0.0", "docs": "/docs"}
