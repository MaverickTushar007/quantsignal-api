"""
Market regime detection — determines if market is trending up, down, or ranging.
"""
import logging
import yfinance as yf
import numpy as np

logger = logging.getLogger(__name__)

def detect_regime(symbol: str) -> dict:
    try:
        data = yf.download(symbol, period="1y", interval="1d",
                           progress=False, auto_adjust=True)
        if len(data) < 50:
            return {"regime": "unknown", "reason": "insufficient data"}

        close = data["Close"].squeeze()
        sma50  = float(close.rolling(50).mean().iloc[-1])
        sma200 = float(close.rolling(200).mean().iloc[-1])
        price  = float(close.iloc[-1])
        ret_20 = float((close.iloc[-1] - close.iloc[-20]) / close.iloc[-20])

        daily_returns = close.pct_change().dropna()
        recent = daily_returns.iloc[-20:]
        trend_strength = float(abs(recent.mean()) / recent.std()) if recent.std() != 0 else 0

        if price > sma50 > sma200 and ret_20 > 0.02:
            regime = "bull"
        elif price < sma50 < sma200 and ret_20 < -0.02:
            regime = "bear"
        else:
            regime = "ranging"

        return {
            "regime": regime,
            "price": round(price, 4),
            "sma50": round(sma50, 4),
            "sma200": round(sma200, 4),
            "return_20d": round(ret_20, 4),
            "trend_strength": round(trend_strength, 4),
            "signal_bias": _bias(regime),
        }

    except Exception as e:
        logger.error(f"[regime] {symbol} failed: {e}")
        return {"regime": "unknown", "reason": str(e)}

def _bias(regime: str) -> str:
    return {
        "bull": "favor BUY signals",
        "bear": "favor SELL signals",
        "ranging": "reduce position size, expect mean reversion",
    }.get(regime, "neutral")

def regime_multiplier(regime: str, direction: str) -> float:
    if regime == "bull" and direction == "BUY":
        return 1.3
    if regime == "bear" and direction == "SELL":
        return 1.3
    if regime == "bull" and direction == "SELL":
        return 0.5
    if regime == "bear" and direction == "BUY":
        return 0.5
    return 1.0
