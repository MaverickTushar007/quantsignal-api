"""
app/domain/signal/confluence_v2.py

14-factor confluence scorecard.
Factors 1-9  → original technical indicators (unchanged logic)
Factor 10    → Equal Highs / Equal Lows  (liquidity magnet — stop cluster detection)
Factor 11    → BOS / CHoCH              (market structure break confirmation)
Factor 12    → Liquidity Sweep          (wick-through + close-back reversal)
Factor 13    → Order Block               (last opposing candle before impulse)

Session multiplier (crypto/global only):
  London / NY open  → score weight ×1.2
  Asia session      → score weight ×0.8
  NSE equities      → no session multiplier (always ×1.0)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

# ── Asset type → session weighting ───────────────────────────────────────────
# Matches the "type" field in TICKER_MAP
_SESSION_ELIGIBLE = {"crypto", "CRYPTO", "global", "GLOBAL", "forex", "FOREX", "commodity", "COMMODITY"}

# UTC hour ranges (inclusive start, exclusive end)
_LONDON_OPEN  = (7, 16)   # 07:00–16:00 UTC
_NY_OPEN      = (13, 21)  # 13:00–21:00 UTC  (overlaps London 13-16 = peak)


# ─────────────────────────────────────────────────────────────────────────────
# SESSION HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _current_session(asset_type: str) -> tuple[str, float]:
    """
    Returns (session_name, weight_multiplier).
    NSE equities always return ("nse", 1.0).
    """
    if asset_type.lower() not in _SESSION_ELIGIBLE:
        return "nse", 1.0

    hour = datetime.now(timezone.utc).hour
    in_london = _LONDON_OPEN[0] <= hour < _LONDON_OPEN[1]
    in_ny     = _NY_OPEN[0]     <= hour < _NY_OPEN[1]

    if in_london or in_ny:
        session = "london_ny"
        mult    = 1.2
    else:
        session = "asia"
        mult    = 0.8

    return session, mult


# ─────────────────────────────────────────────────────────────────────────────
# FACTOR 10 — EQUAL HIGHS / EQUAL LOWS  (liquidity magnet)
# ─────────────────────────────────────────────────────────────────────────────

def _detect_equal_levels(
    df: pd.DataFrame,
    lookback: int = 50,
    tolerance_pct: float = 0.002,   # 0.2% — tight enough to be real clusters
) -> dict:
    """
    Equal highs   → buy-side liquidity sitting above (bearish magnet).
    Equal lows    → sell-side liquidity sitting below (bullish magnet).

    Bullish signal: equal lows detected below current price
                    → stops are clustered below, price likely sweeps down then reverses up.
    Bearish signal: equal highs detected above current price
                    → stops are clustered above, price likely sweeps up then reverses down.

    Returns dict with keys: detected, type ("equal_lows"|"equal_highs"|None),
                             level, distance_pct, signal ("BULLISH"|"BEARISH"|"NEUTRAL")
    """
    if len(df) < lookback + 1:
        return {"detected": False, "signal": "NEUTRAL", "type": None, "value": "—"}

    window  = df.iloc[-(lookback + 1):-1]   # exclude last candle
    current = float(df["Close"].iloc[-1])
    tol     = current * tolerance_pct

    highs = window["High"].values
    lows  = window["Low"].values

    # Find clusters: pairs (or more) within tolerance
    def _has_cluster(prices: np.ndarray, tol: float) -> tuple[bool, float]:
        sorted_p = np.sort(prices)
        for i in range(len(sorted_p) - 1):
            if abs(sorted_p[i + 1] - sorted_p[i]) <= tol:
                return True, float(round((sorted_p[i] + sorted_p[i + 1]) / 2, 4))
        return False, 0.0

    eq_lows,  level_low  = _has_cluster(lows,  tol)
    eq_highs, level_high = _has_cluster(highs, tol)

    if eq_lows and level_low < current and abs(current - level_low) / current <= 0.03:
        dist = round(abs(current - level_low) / current * 100, 2)
        return {
            "detected":     True,
            "signal":       "BULLISH",   # sell-side liquidity below = bullish magnet
            "type":         "equal_lows",
            "level":        level_low,
            "distance_pct": dist,
            "value":        f"Equal lows @ {level_low:.2f} ({dist:.1f}% below)",
        }

    if eq_highs and level_high > current and abs(level_high - current) / current <= 0.03:
        dist = round(abs(level_high - current) / current * 100, 2)
        return {
            "detected":     True,
            "signal":       "BEARISH",   # buy-side liquidity above = bearish magnet
            "type":         "equal_highs",
            "level":        level_high,
            "distance_pct": dist,
            "value":        f"Equal highs @ {level_high:.2f} ({dist:.1f}% above)",
        }

    return {"detected": False, "signal": "NEUTRAL", "type": None, "value": "None detected"}


# ─────────────────────────────────────────────────────────────────────────────
# FACTOR 11 — BOS / CHoCH  (market structure)
# ─────────────────────────────────────────────────────────────────────────────

def _detect_bos_choch(
    df: pd.DataFrame,
    swing_lookback: int = 10,   # candles each side to qualify as swing H/L
    structure_window: int = 60,
) -> dict:
    """
    BOS  (Break of Structure) : price breaks the most recent swing high/low
         in the direction of the prevailing trend — trend continuation.
    CHoCH (Change of Character): price breaks against the prevailing trend —
         early reversal signal.

    Bullish signals: Bullish BOS (uptrend continues) or Bullish CHoCH (downtrend reversing).
    Bearish signals: Bearish BOS (downtrend continues) or Bearish CHoCH (uptrend reversing).
    """
    if len(df) < structure_window + swing_lookback:
        return {"detected": False, "signal": "NEUTRAL", "type": None, "value": "Insufficient data"}

    window  = df.iloc[-structure_window:]
    highs   = window["High"].values
    lows    = window["Low"].values
    closes  = window["Close"].values
    n       = len(closes)
    sl      = swing_lookback

    # Identify swing highs and lows
    swing_highs, swing_lows = [], []
    for i in range(sl, n - sl):
        if highs[i] == max(highs[i - sl: i + sl + 1]):
            swing_highs.append((i, highs[i]))
        if lows[i] == min(lows[i - sl: i + sl + 1]):
            swing_lows.append((i, lows[i]))

    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return {"detected": False, "signal": "NEUTRAL", "type": None, "value": "No clear swings"}

    last_sh = swing_highs[-1][1]
    last_sl = swing_lows[-1][1]
    prev_sh = swing_highs[-2][1]
    prev_sl = swing_lows[-2][1]
    current = closes[-1]

    # Prevailing trend from swing sequence
    hh = last_sh > prev_sh   # higher high
    hl = last_sl > prev_sl   # higher low
    ll = last_sl < prev_sl   # lower low
    lh = last_sh < prev_sh   # lower high

    uptrend   = hh and hl
    downtrend = ll and lh

    # BOS / CHoCH detection on latest close
    if current > last_sh:
        if uptrend:
            return {"detected": True, "signal": "BULLISH", "type": "BOS_bullish",
                    "value": f"Bullish BOS — broke {last_sh:.2f}"}
        else:
            return {"detected": True, "signal": "BULLISH", "type": "CHoCH_bullish",
                    "value": f"Bullish CHoCH — reversal signal @ {last_sh:.2f}"}

    if current < last_sl:
        if downtrend:
            return {"detected": True, "signal": "BEARISH", "type": "BOS_bearish",
                    "value": f"Bearish BOS — broke {last_sl:.2f}"}
        else:
            return {"detected": True, "signal": "BEARISH", "type": "CHoCH_bearish",
                    "value": f"Bearish CHoCH — reversal signal @ {last_sl:.2f}"}

    return {"detected": False, "signal": "NEUTRAL", "type": None,
            "value": f"Range {last_sl:.2f}–{last_sh:.2f}"}


# ─────────────────────────────────────────────────────────────────────────────
# FACTOR 12 — LIQUIDITY SWEEP
# ─────────────────────────────────────────────────────────────────────────────

# Asset-calibrated sweep tolerances (from GoldQuant adapter)
_SWEEP_TOLERANCE = {
    "crypto":    lambda price: price * 0.001,   # 0.1%
    "global":    lambda price: price * 0.001,   # 0.1%  (XAU, indices)
    "commodity": lambda price: price * 0.001,
    "forex":     lambda price: price * 0.0005,  # tighter for forex
    "nse_eq":    lambda price: price * 0.002,   # 0.2% for NSE equities
    "default":   lambda price: price * 0.001,
}

def _detect_liquidity_sweep(
    df: pd.DataFrame,
    asset_type: str = "default",
    lookback: int = 20,
) -> dict:
    """
    Wick-through + close-back sweep detection (GoldQuant logic, multi-asset adapted).

    Sell-side sweep (bullish): wick below prior swing low, closes back above it.
    Buy-side  sweep (bearish): wick above prior swing high, closes back below it.
    """
    if len(df) < lookback + 1:
        return {"detected": False, "signal": "NEUTRAL", "value": "Insufficient data"}

    recent     = df.iloc[-1]
    prior      = df.iloc[-(lookback + 1):-1]
    price      = float(recent["Close"])

    tol_fn     = _SWEEP_TOLERANCE.get(asset_type, _SWEEP_TOLERANCE["default"])
    threshold  = tol_fn(price)

    swing_low  = float(prior["Low"].min())
    swing_high = float(prior["High"].max())

    # Sell-side sweep → bullish reversal
    sell_swept = (
        float(recent["Low"])  <= swing_low  - threshold and
        float(recent["Close"]) > swing_low
    )

    # Buy-side sweep → bearish reversal
    buy_swept = (
        float(recent["High"]) >= swing_high + threshold and
        float(recent["Close"]) < swing_high
    )

    if sell_swept:
        swept_dist = round(abs(float(recent["Low"]) - swing_low) / price * 100, 3)
        return {
            "detected":   True,
            "signal":     "BULLISH",
            "type":       "sell_side_sweep",
            "swept_level": swing_low,
            "value":      f"Sell-side sweep of {swing_low:.2f} ({swept_dist:.2f}% wick)",
        }

    if buy_swept:
        swept_dist = round(abs(float(recent["High"]) - swing_high) / price * 100, 3)
        return {
            "detected":   True,
            "signal":     "BEARISH",
            "type":       "buy_side_sweep",
            "swept_level": swing_high,
            "value":      f"Buy-side sweep of {swing_high:.2f} ({swept_dist:.2f}% wick)",
        }

    return {"detected": False, "signal": "NEUTRAL", "value": "No sweep detected"}




# ─────────────────────────────────────────────────────────────────────────────
# FACTOR 13 — ORDER BLOCKS
# ─────────────────────────────────────────────────────────────────────────────

def _detect_order_block(
    df: pd.DataFrame,
    lookback: int = 40,
    impulse_threshold: float = 0.015,  # 1.5% move to qualify as impulse
) -> dict:
    """
    Order Block: the last opposing candle before a strong impulse move.

    Bullish OB: last bearish candle before a strong bullish impulse
                → price returning to this zone is a buy opportunity.
    Bearish OB: last bullish candle before a strong bearish impulse
                → price returning to this zone is a sell opportunity.

    Signal fires when current price is INSIDE or just above/below the OB zone.
    """
    if len(df) < lookback + 3:
        return {"detected": False, "signal": "NEUTRAL", "value": "Insufficient data"}

    window  = df.iloc[-lookback:].reset_index(drop=True)
    current = float(df["Close"].iloc[-1])
    n       = len(window)

    bullish_ob = None
    bearish_ob = None

    for i in range(1, n - 2):
        # Calculate move after candle i
        move = (window["Close"].iloc[i + 1] - window["Close"].iloc[i]) / window["Close"].iloc[i]

        if move >= impulse_threshold:
            # Strong bullish impulse — look for last bearish candle before it
            if window["Close"].iloc[i] < window["Open"].iloc[i]:  # bearish candle
                bullish_ob = {
                    "high": float(window["High"].iloc[i]),
                    "low":  float(window["Low"].iloc[i]),
                    "idx":  i,
                }

        elif move <= -impulse_threshold:
            # Strong bearish impulse — look for last bullish candle before it
            if window["Close"].iloc[i] > window["Open"].iloc[i]:  # bullish candle
                bearish_ob = {
                    "high": float(window["High"].iloc[i]),
                    "low":  float(window["Low"].iloc[i]),
                    "idx":  i,
                }

    # Check if current price is inside or approaching an OB zone (within 0.5%)
    proximity = current * 0.005

    if bullish_ob:
        ob_mid = (bullish_ob["high"] + bullish_ob["low"]) / 2
        if bullish_ob["low"] - proximity <= current <= bullish_ob["high"] + proximity:
            return {
                "detected": True,
                "signal":   "BULLISH",
                "type":     "bullish_ob",
                "zone":     (bullish_ob["low"], bullish_ob["high"]),
                "value":    f"Bullish OB zone {bullish_ob['low']:.2f}–{bullish_ob['high']:.2f} (price inside)",
            }

    if bearish_ob:
        ob_mid = (bearish_ob["high"] + bearish_ob["low"]) / 2
        if bearish_ob["low"] - proximity <= current <= bearish_ob["high"] + proximity:
            return {
                "detected": True,
                "signal":   "BEARISH",
                "type":     "bearish_ob",
                "zone":     (bearish_ob["low"], bearish_ob["high"]),
                "value":    f"Bearish OB zone {bearish_ob['low']:.2f}–{bearish_ob['high']:.2f} (price inside)",
            }

    # OB exists but price not in zone — show nearest
    if bullish_ob and (not bearish_ob or bullish_ob["idx"] > bearish_ob.get("idx", -1)):
        return {
            "detected": False,
            "signal":   "NEUTRAL",
            "value":    f"Nearest bullish OB: {bullish_ob['low']:.2f}–{bullish_ob['high']:.2f} (not in zone)",
        }
    if bearish_ob:
        return {
            "detected": False,
            "signal":   "NEUTRAL",
            "value":    f"Nearest bearish OB: {bearish_ob['low']:.2f}–{bearish_ob['high']:.2f} (not in zone)",
        }

    return {"detected": False, "signal": "NEUTRAL", "value": "No OB detected"}

# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC: 12-FACTOR CONFLUENCE BUILDER
# ─────────────────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────────
# FACTOR 14 — VWAP DISTANCE + SLOPE
# ─────────────────────────────────────────────────────────────────────────────

def _detect_vwap(df: pd.DataFrame) -> dict:
    """
    VWAP = cumulative(price * volume) / cumulative(volume) over session.
    Bullish: price above VWAP and VWAP sloping up.
    Bearish: price below VWAP or VWAP sloping down.
    """
    if len(df) < 10 or "Volume" not in df.columns:
        return {"signal": "NEUTRAL", "value": "Insufficient data"}

    try:
        typical = (df["High"] + df["Low"] + df["Close"]) / 3
        vol = df["Volume"].replace(0, np.nan).fillna(1)
        cum_vol = vol.cumsum()
        vwap_series = (typical * vol).cumsum() / cum_vol

        current  = float(df["Close"].iloc[-1])
        vwap_now = float(vwap_series.iloc[-1])
        vwap_10  = float(vwap_series.iloc[-10])

        dist_pct = (current - vwap_now) / vwap_now * 100
        slope    = (vwap_now - vwap_10) / vwap_10 * 100  # % change over 10 bars

        above = current > vwap_now
        sloping_up = slope > 0

        if above and sloping_up:
            signal = "BULLISH"
        elif not above and not sloping_up:
            signal = "BEARISH"
        else:
            signal = "NEUTRAL"

        direction = "above" if above else "below"
        slope_str = f"+{slope:.2f}%" if slope >= 0 else f"{slope:.2f}%"
        value = f"{dist_pct:+.2f}% {direction} VWAP (slope {slope_str})"

        return {"signal": signal, "value": value}
    except Exception:
        return {"signal": "NEUTRAL", "value": "VWAP error"}

def build_confluence_v2(
    feat_row: dict,
    df: pd.DataFrame,
    asset_type: str = "default",
) -> tuple[list[dict], int, str, str]:
    """
    Builds the full 14-factor confluence scorecard.

    Args:
        feat_row   : latest row from build_features() as dict
        df         : raw OHLCV DataFrame (needed for structural factors)
        asset_type : from TICKER_MAP["type"] — drives session mult + sweep tolerance

    Returns:
        factors        : list of 12 confluence dicts (name, value, signal)
        bull_count     : int  0–14
        score_label    : str  e.g. "9/14 bullish"
        session_info   : str  e.g. "london_ny (×1.2)" or "nse (×1.0)"
    """
    # ── Factors 1-9 (original) ────────────────────────────────────────────
    rsi    = float(feat_row.get("rsi_14",      50))
    macd   = float(feat_row.get("macd",         0))    # normalised MACD
    bbpct  = float(feat_row.get("bb_pct",      0.5)) * 100
    volr   = float(feat_row.get("volume_ratio_10d",    1))
    dsma50 = float(feat_row.get("price_to_sma50",   0))
    mom5   = float(feat_row.get("return_5d",       0)) * 100
    mom20  = float(feat_row.get("return_20d",      0)) * 100
    mrevz  = float(feat_row.get("mean_rev_z",   0))
    body   = float(feat_row.get("body_ratio",  0.5))

    def sig(bull: bool) -> str:
        return "BULLISH" if bull else "BEARISH"

    factors: list[dict] = [
        # 1
        {
            "name":   "RSI-14",
            "value":  f"{rsi:.0f} — {'Oversold' if rsi < 35 else 'Overbought' if rsi > 65 else 'Neutral'}",
            "signal": sig(rsi < 50),
            "tier":   3,
        },
        # 2  (normalised MACD — positive = bullish)
        {
            "name":   "MACD",
            "value":  f"{'Bullish' if macd > 0 else 'Bearish'} ({macd:+.5f})",
            "signal": sig(macd > 0),
            "tier":   3,
        },
        # 3
        {
            "name":   "Bollinger",
            "value":  f"{bbpct:.0f}% ({'Upper' if bbpct > 80 else 'Lower' if bbpct < 20 else 'Mid'})",
            "signal": sig(bbpct < 50),
            "tier":   3,
        },
        # 4
        {
            "name":   "Volume",
            "value":  f"{volr:.2f}× avg",
            "signal": sig(volr > 1),
            "tier":   3,
        },
        # 5
        {
            "name":   "vs SMA50",
            "value":  f"{'Above' if dsma50 > 0 else 'Below'} SMA50 ({dsma50 * 100:+.2f}%)",
            "signal": sig(dsma50 > 0),
            "tier":   3,
        },
        # 6
        {
            "name":   "5D Momentum",
            "value":  f"{mom5:+.2f}% ROC",
            "signal": sig(mom5 > 0),
            "tier":   3,
        },
        # 7
        {
            "name":   "20D Momentum",
            "value":  f"{mom20:+.2f}% ROC",
            "signal": sig(mom20 > 0),
            "tier":   3,
        },
        # 8 — mean reversion: negative z = oversold = bullish
        {
            "name":   "Mean Rev Z",
            "value":  f"{mrevz:+.2f} ATR units ({'Oversold' if mrevz < -1 else 'Overbought' if mrevz > 1 else 'Neutral'})",
            "signal": sig(mrevz < 0),
            "tier":   3,
        },
        # 9 — body ratio: conviction candle in direction of mom5
        {
            "name":   "Candle Conviction",
            "value":  f"{body:.2f} body ratio ({'Strong' if body > 0.6 else 'Weak' if body < 0.3 else 'Moderate'})",
            "signal": sig(body > 0.4),  # any conviction candle is mildly bullish
            "tier":   3,
        },
    ]

    # ── Factor 10 — Equal Highs / Lows ───────────────────────────────────
    eq = _detect_equal_levels(df, lookback=50)
    factors.append({
        "name":   "Equal H/L (Liquidity)",
        "value":  eq["value"],
        "signal": eq["signal"] if eq["detected"] else "NEUTRAL",
        "tier":   1,
    })

    # ── Factor 11 — BOS / CHoCH ──────────────────────────────────────────
    bos = _detect_bos_choch(df)
    factors.append({
        "name":   "Market Structure",
        "value":  bos["value"],
        "signal": bos["signal"] if bos["detected"] else "NEUTRAL",
        "tier":   1,
    })

    # ── Factor 12 — Liquidity Sweep ──────────────────────────────────────
    _type_map = {
        "crypto":    "crypto",
        "global":    "global",
        "commodity": "commodity",
        "forex":     "forex",
        "nse_eq":    "nse_eq",
        "equity":    "nse_eq",
        "etf":       "nse_eq",
    }
    sweep_type = _type_map.get(asset_type, "default")
    sweep = _detect_liquidity_sweep(df, asset_type=sweep_type)
    factors.append({
        "name":   "Liquidity Sweep",
        "value":  sweep["value"],
        "signal": sweep["signal"] if sweep["detected"] else "NEUTRAL",
        "tier":   1,
    })

    # ── Factor 13 — Order Block ───────────────────────────────────────────
    ob = _detect_order_block(df)
    factors.append({
        "name":   "Order Block",
        "value":  ob["value"],
        "signal": ob["signal"] if ob["detected"] else "NEUTRAL",
        "tier":   1,
    })

    # ── Factor 14 — VWAP Distance + Slope ────────────────────────────────
    vwap = _detect_vwap(df)
    factors.append({
        "name":   "VWAP",
        "value":  vwap["value"],
        "signal": vwap["signal"],
        "tier":   2,
    })


    # ── Tier tags on existing factors ─────────────────────────────────────
    # Tier 1: structural/liquidity (factors 10-13) — already tagged above
    # Tier 2: volume confirmation (factors 4, 9)
    # Tier 3: indicators (factors 1-3, 5-8)
    tier_map = {
        "RSI-14":              3,
        "macd":                3,
        "Bollinger":           3,
        "Volume":              2,
        "vs SMA50":            3,
        "5D Momentum":         3,
        "20D Momentum":        3,
        "Mean Rev Z":          3,
        "Candle Conviction":   2,
        "Equal H/L (Liquidity)": 1,
        "Market Structure":    1,
        "Liquidity Sweep":     1,
        "Order Block":         1,
    }
    for f in factors:
        if "tier" not in f:
            f["tier"] = tier_map.get(f["name"], 3)

    # ── Weighted Scoring ──────────────────────────────────────────────────
    # Tier 1 = 3pts, Tier 2 = 2pts, Tier 3 = 1pt
    # CAP RULE: if Tier 1 bulls = 0 AND Tier 1 bears = 0 → NEUTRAL regardless
    tier_weights = {1: 3, 2: 2, 3: 1}

    tier1_factors = [f for f in factors if f["tier"] == 1]
    tier1_bull = sum(1 for f in tier1_factors if f["signal"] == "BULLISH")
    tier1_bear = sum(1 for f in tier1_factors if f["signal"] == "BEARISH")
    tier1_active = tier1_bull + tier1_bear  # neutral tier1 dont count

    weighted_bull = sum(tier_weights[f["tier"]] for f in factors if f["signal"] == "BULLISH")
    weighted_bear = sum(tier_weights[f["tier"]] for f in factors if f["signal"] == "BEARISH")
    max_weighted  = sum(tier_weights[f["tier"]] for f in factors)  # 4×3 + 3×2 + 7×1 = 25

    # Raw bull count for display
    bull_count = sum(1 for f in factors if f["signal"] == "BULLISH")

    # Normalised weighted score 0-13 (scaled to old 0-12 range for enforce_consistency_v2)
    weighted_score = round((weighted_bull / max_weighted) * 13) if max_weighted > 0 else 0

    # CAP: no Tier 1 signal active → clamp to NEUTRAL zone (5-7)
    if tier1_active == 0:
        weighted_score = min(weighted_score, 6)

    # Session multiplier
    session, session_mult = _current_session(asset_type)
    session_info = f"{session} (×{session_mult:.1f})"

    score_label = f"{bull_count}/13 bullish (weighted: {weighted_score}/13)"

    return factors, bull_count, score_label, session_info


# ─────────────────────────────────────────────────────────────────────────────
# UPDATED CONSISTENCY ENFORCER  (12-factor scale)
# ─────────────────────────────────────────────────────────────────────────────

def enforce_consistency_v2(
    direction: str,
    probability: float,
    bull_count: int,
    session_mult: float = 1.0,
) -> tuple[str, float, float]:
    """
    Weighted 13-factor confluence enforcer.

    bull_count here is the WEIGHTED score (0-13), not raw factor count.
    Tier 1 (liquidity/structure) factors dominate — cap rule applied upstream.

    Thresholds on weighted score × session_mult:
      0–3   → SELL  (strong bearish — structure against)
      4–5   → SELL  (moderate bearish)
      6     → HOLD  (no structural edge)
      7     → HOLD  (weak bias)
      8–9   → BUY   (structure + confirmation agree)
      10–13 → BUY   (high conviction — Tier 1 strongly bullish)

    session_mult: 1.2 London/NY crypto, 0.8 Asia, 1.0 NSE.
    """
    effective = bull_count * session_mult
    confluence_agreement = round(bull_count / 13, 3)

    if effective <= 2:
        enforced_dir  = "SELL"
        enforced_prob = round(min(probability, 0.38) * 0.85 + 0.10, 4)

    elif effective <= 4:
        enforced_dir  = "SELL"
        enforced_prob = round(min(probability, 0.44), 4)

    elif effective <= 5:
        enforced_dir  = "HOLD"
        enforced_prob = 0.46

    elif effective <= 6:
        enforced_dir  = "HOLD"
        enforced_prob = 0.50

    elif effective <= 8:
        enforced_dir  = direction if direction == "BUY" else "HOLD"
        enforced_prob = round(max(probability, 0.55), 4) if enforced_dir == "BUY" else 0.51

    else:  # 10-13 weighted — Tier 1 must be contributing here
        enforced_dir  = "BUY"
        enforced_prob = round(max(probability, 0.65), 4)

    enforced_prob = round(max(0.01, min(0.99, enforced_prob)), 4)
    return enforced_dir, enforced_prob, confluence_agreement
