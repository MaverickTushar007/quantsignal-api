"""
api/schemas.py
Pydantic models — every API response is validated against these.
"""

from pydantic import BaseModel
from typing import List, Optional
from enum import Enum


class Direction(str, Enum):
    BUY  = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class ConfluenceItem(BaseModel):
    name:   str
    value:  str
    signal: str


class NewsItem(BaseModel):
    title:     str
    source:    str
    sentiment: str
    url:       str


class SignalResponse(BaseModel):
    model_config = {"extra": "allow"}  # pass-through any extra fields like mtf
    symbol:          str
    display:         str
    name:            str
    type:            str
    icon:            str
    direction:       Direction
    probability:     float
    confidence:      str
    kelly_size:      float
    expected_value:  float
    take_profit:     float
    stop_loss:       float
    current_price:   float
    risk_reward:     float
    atr:             float
    model_agreement: float
    top_features:    List[str]
    confluence:      List[ConfluenceItem]
    confluence_score:str
    news:            List[NewsItem]
    reasoning:       str
    generated_at:    str


class WatchlistItem(BaseModel):
    symbol:        str
    display:       str
    name:          str
    type:          str
    icon:          str
    direction:     Direction
    probability:   float
    confidence:    str
    current_price: float
    kelly_size:    float


class MarketMood(BaseModel):
    mood:           str
    buy_count:      int
    sell_count:     int
    hold_count:     int
    avg_confidence: float
    total:          int


class BacktestSummary(BaseModel):
    ticker:       str
    win_rate:     float
    avg_return:   float
    sharpe:       float
    max_drawdown: float
    total_return: float
    n_trades:     int


class HealthResponse(BaseModel):
    status:  str
    version: str
    env:     str
