"""
Market regime detection — determines if market is trending up, down, or ranging.
Multipliers calibrated from actual signal outcomes (140 closed signals).
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
        "bull":    "favor BUY signals",
        "bear":    "favor SELL signals",
        "ranging": "favor SELL signals, suppress BUY",
    }.get(regime, "neutral")


def regime_multiplier(regime: str, direction: str) -> float:
    """
    Multipliers derived from actual win rates (140 closed signals):

    regime    dir   actual_win%   multiplier   logic
    -------   ---   -----------   -----------  ------
    bear      BUY   5.6%          0.25x        near-suppress, rarely wins
    bear      SELL  55.6%         1.5x         strong edge, boost it
    bull      BUY   100% (1 smpl) 1.3x         keep conservative, tiny sample
    bull      SELL  0.0% (5 smpl) 0.2x         suppress hard
    ranging   BUY   10.6%         0.4x         suppress, most ranging BUYs lose
    ranging   SELL  57.1%         1.4x         solid edge, boost it
    unknown   *     ~0%           0.5x         no data, be cautious
    """
    table = {
        ("bear",    "BUY"):  0.25,
        ("bear",    "SELL"): 1.5,
        ("bull",    "BUY"):  1.3,
        ("bull",    "SELL"): 0.2,
        ("ranging", "BUY"):  0.4,
        ("ranging", "SELL"): 1.4,
        ("unknown", "BUY"):  0.5,
        ("unknown", "SELL"): 0.5,
    }
    return table.get((regime, direction), 1.0)
