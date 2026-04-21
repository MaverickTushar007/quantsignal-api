"""
app/domain/ml/walk_forward.py
Walk-forward validation for the ML model.
80/20 IS/OOS split per symbol. Flags overfitting when OOS win rate
drops below 50% of IS win rate (WFE < 0.5).
"""
from __future__ import annotations
import logging
from dataclasses import dataclass
import pandas as pd

log = logging.getLogger(__name__)

CRYPTO_SYMBOLS = {
    "BTC-USD","ETH-USD","BNB-USD","SOL-USD","ADA-USD",
    "AVAX-USD","DOT-USD","DOGE-USD","XRP-USD","OP-USD",
    "ATOM-USD","RENDER-USD","MATIC-USD","LINK-USD",
}

@dataclass
class WFVResult:
    symbol:           str
    is_win_rate:      float
    oos_win_rate:     float
    wfe_ratio:        float        # oos / is — below 0.5 = overfitting
    is_trades:        int
    oos_trades:       int
    is_overfitted:    bool
    insufficient_data: bool


def _fetch_long_history(symbol: str) -> pd.DataFrame | None:
    """Fetch 2y of daily candles, bypassing CoinGecko 180-day cap for crypto."""
    try:
        if symbol in CRYPTO_SYMBOLS:
            from app.domain.data.multi_source import _fetch_yahoo_direct
            df = _fetch_yahoo_direct(symbol, "2y")
            if df is not None and len(df) > 100:
                return df
        from app.domain.data.market import fetch_ohlcv
        return fetch_ohlcv(symbol, "2y")
    except Exception as e:
        log.warning(f"[WFV] fetch failed for {symbol}: {e}")
        return None


def _simulate_signals(df: pd.DataFrame, symbol: str) -> list[dict]:
    """Run ML predict on rolling 180-bar windows, collect outcomes."""
    from app.domain.ml.ensemble import predict

    results = []
    window = 90
    step   = 15   # advance 15 bars between windows

    for start in range(0, len(df) - window - 5, step):
        end   = start + window
        chunk = df.iloc[start:end].copy()
        chunk.index = range(len(chunk))

        sig = predict(symbol, chunk)
        if sig is None:
            # No ML model locally — use simple price momentum as proxy
            # BUY if last 10-bar return > 0, SELL otherwise
            ret = float(chunk["Close"].iloc[-1]) / float(chunk["Close"].iloc[-10]) - 1
            direction = "BUY" if ret > 0 else "SELL"
            close = float(chunk["Close"].iloc[-1])
            atr = float((chunk["High"] - chunk["Low"]).rolling(14).mean().iloc[-1])
            if atr <= 0:
                continue
            tp = close + 2.0 * atr if direction == "BUY" else close - 2.0 * atr
            sl = close - 1.0 * atr if direction == "BUY" else close + 1.0 * atr

            class _Proxy:
                pass
            sig = _Proxy()
            sig.current_price = close
            sig.take_profit = tp
            sig.stop_loss = sl
            sig.direction = direction

        # Check outcome on the next 5 bars after the window
        future = df.iloc[end:end + 5]
        if len(future) < 1:
            continue

        entry = sig.current_price
        tp    = sig.take_profit
        sl    = sig.stop_loss
        direction = sig.direction

        outcome = "open"
        for _, bar in future.iterrows():
            hi = float(bar["High"])
            lo = float(bar["Low"])
            if direction == "BUY":
                if lo <= sl:
                    outcome = "loss"; break
                if hi >= tp:
                    outcome = "win";  break
            else:
                if hi >= sl:
                    outcome = "loss"; break
                if lo <= tp:
                    outcome = "win";  break

        if outcome != "open":
            results.append({"outcome": outcome, "idx": start})

    return results


def _win_rate(trades: list[dict]) -> float:
    if not trades:
        return 0.0
    wins = sum(1 for t in trades if t["outcome"] == "win")
    return round(wins / len(trades), 4)


def validate(symbol: str) -> WFVResult:
    """Run 80/20 walk-forward validation for a single symbol."""
    df = _fetch_long_history(symbol)

    if df is None or len(df) < 250:
        log.warning(f"[WFV] {symbol} — insufficient data ({len(df) if df is not None else 0} bars)")
        return WFVResult(
            symbol=symbol, is_win_rate=0, oos_win_rate=0,
            wfe_ratio=0, is_trades=0, oos_trades=0,
            is_overfitted=False, insufficient_data=True
        )

    split = int(len(df) * 0.8)
    is_df  = df.iloc[:split].reset_index(drop=True)
    oos_df = df.iloc[split:].reset_index(drop=True)

    log.info(f"[WFV] {symbol} — IS={len(is_df)} bars OOS={len(oos_df)} bars")

    is_trades  = _simulate_signals(is_df,  symbol)
    oos_trades = _simulate_signals(oos_df, symbol)

    if len(is_trades) < 5 or len(oos_trades) < 3:
        log.warning(f"[WFV] {symbol} — too few trades IS={len(is_trades)} OOS={len(oos_trades)}")
        return WFVResult(
            symbol=symbol,
            is_win_rate=_win_rate(is_trades),
            oos_win_rate=_win_rate(oos_trades),
            wfe_ratio=0, is_trades=len(is_trades),
            oos_trades=len(oos_trades),
            is_overfitted=False, insufficient_data=True
        )

    is_wr  = _win_rate(is_trades)
    oos_wr = _win_rate(oos_trades)
    wfe    = round(oos_wr / is_wr, 4) if is_wr > 0 else 0
    overfitted = wfe < 0.5 and len(oos_trades) >= 3

    log.info(
        f"[WFV] {symbol} IS_wr={is_wr} OOS_wr={oos_wr} "
        f"WFE={wfe} overfitted={overfitted}"
    )

    return WFVResult(
        symbol=symbol, is_win_rate=is_wr, oos_win_rate=oos_wr,
        wfe_ratio=wfe, is_trades=len(is_trades), oos_trades=len(oos_trades),
        is_overfitted=overfitted, insufficient_data=False
    )


def validate_all(symbols: list[str]) -> dict[str, WFVResult]:
    """Run walk-forward validation across multiple symbols."""
    results = {}
    for sym in symbols:
        try:
            results[sym] = validate(sym)
        except Exception as e:
            log.error(f"[WFV] {sym} failed: {e}")
    return results
