"""
app/domain/ml/labeling.py

Triple-barrier labeling + meta-labeling for QuantSignal.
Replaces the naive pct_change threshold in ensemble.py with
volatility-aware labels that reflect actual trade outcomes.

Triple Barrier Logic (from AFML Chapter 3):
  - Upper barrier: close + pt_sl[0] * daily_vol * close  (profit target)
  - Lower barrier: close - pt_sl[1] * daily_vol * close  (stop loss)
  - Vertical barrier: t1 (time cutoff)
  - Label = +1 if upper hit first, -1 if lower hit first, 0 if time cutoff

Meta-labeling (from AFML Chapter 3.6):
  - Primary model generates direction (BUY/SELL)
  - Secondary model predicts WHETHER primary model is correct
  - Output: binary (1 = take trade, 0 = skip) + confidence score
"""

import numpy as np
import pandas as pd
from typing import Optional, Tuple


# ─────────────────────────────────────────────
# 1. DAILY VOLATILITY (base unit for barriers)
# ─────────────────────────────────────────────

def get_daily_vol(close: pd.Series, span: int = 20) -> pd.Series:
    """
    Compute daily log-return volatility using EWM.
    Used to scale barrier widths dynamically per asset.

    Args:
        close: pd.Series of closing prices
        span:  EWM span (default 20 days)

    Returns:
        pd.Series of daily vol estimates (same index as close)
    """
    log_ret = np.log(close / close.shift(1)).dropna()
    vol = log_ret.ewm(span=span).std()
    return vol.reindex(close.index).ffill()


# ─────────────────────────────────────────────
# 2. VERTICAL BARRIER (time cutoff)
# ─────────────────────────────────────────────

def get_vertical_barrier(
    close: pd.Series,
    t_events: pd.DatetimeIndex,
    num_days: int = 5,
) -> pd.Series:
    """
    For each event in t_events, find the timestamp num_days ahead.
    This forms the right edge of the triple barrier.

    Args:
        close:    price series
        t_events: event timestamps (signal fire dates)
        num_days: max holding period in days

    Returns:
        pd.Series mapping event_date -> vertical_barrier_date
    """
    t1 = close.index.searchsorted(t_events + pd.Timedelta(days=num_days))
    t1 = t1[t1 < close.shape[0]]
    t1 = pd.Series(
        close.index[t1],
        index=t_events[:t1.shape[0]],
        name="t1",
    )
    return t1


# ─────────────────────────────────────────────
# 3. TRIPLE BARRIER LABELING
# ─────────────────────────────────────────────

def get_events(
    close: pd.Series,
    t_events: pd.DatetimeIndex,
    pt_sl: Tuple[float, float],
    target: pd.Series,
    min_ret: float = 0.0,
    num_threads: int = 1,
    t1: Optional[pd.Series] = None,
    side: Optional[pd.Series] = None,
) -> pd.DataFrame:
    """
    Find the first barrier touch for each event in t_events.

    Args:
        close:    price series
        t_events: timestamps where we consider entering a trade
        pt_sl:    (profit_take_mult, stop_loss_mult) — barrier widths as multiples of target
        target:   volatility series (from get_daily_vol) — scales barrier width
        min_ret:  minimum return threshold to consider a valid event
        t1:       vertical barrier series (from get_vertical_barrier)
        side:     primary model side (+1 BUY, -1 SELL) — for meta-labeling only

    Returns:
        DataFrame with columns: t1, trgt, side (if provided)
    """
    # Filter events by minimum return threshold
    target = target.reindex(t_events).dropna()
    target = target[target > min_ret]

    # Vertical barrier
    if t1 is None:
        t1 = pd.Series(pd.NaT, index=t_events)
    t1 = t1.reindex(target.index)

    # Side (for meta-labeling)
    if side is None:
        _side = pd.Series(1.0, index=target.index)  # assume BUY for pure labeling
    else:
        _side = side.reindex(target.index).fillna(1.0)

    events = pd.concat({"t1": t1, "trgt": target, "side": _side}, axis=1).dropna(subset=["trgt"])

    # For each event, find first barrier touch
    out = []
    for loc, (t1_val, trgt, side_val) in events.iterrows():
        path = _get_path(close, loc, t1_val, trgt, pt_sl, side_val)
        out.append(path)

    out_df = pd.DataFrame(out, index=events.index)
    return out_df


def _get_path(
    close: pd.Series,
    loc: pd.Timestamp,
    t1: pd.Timestamp,
    trgt: float,
    pt_sl: Tuple[float, float],
    side: float,
) -> dict:
    """
    For a single event at `loc`, scan price path until first barrier touch.
    Returns dict with: t1 (touch time), sl (stop level), pt (profit level), ret, label
    """
    # Barrier levels
    price_0 = close.loc[loc]
    pt_level = price_0 * (1 + pt_sl[0] * trgt * side)   # profit target
    sl_level = price_0 * (1 - pt_sl[1] * trgt * side)   # stop loss

    # Path: prices between entry and vertical barrier
    if pd.isnull(t1):
        path = close.loc[loc:]
    else:
        path = close.loc[loc:t1]

    # First touch
    pt_touched = path[path * side >= pt_level * side]
    sl_touched = path[path * side <= sl_level * side]

    # Determine which barrier was hit first
    t_pt = pt_touched.index[0] if len(pt_touched) > 0 else pd.NaT
    t_sl = sl_touched.index[0] if len(sl_touched) > 0 else pd.NaT

    if pd.isnull(t_pt) and pd.isnull(t_sl):
        # No barrier hit — vertical barrier (time cutoff)
        touch_time = t1 if not pd.isnull(t1) else path.index[-1]
        label = 0
    elif pd.isnull(t_sl) or (not pd.isnull(t_pt) and t_pt <= t_sl):
        touch_time = t_pt
        label = 1   # profit target hit first
    else:
        touch_time = t_sl
        label = -1  # stop loss hit first

    ret = (close.loc[touch_time] / price_0 - 1) * side if touch_time in close.index else 0.0

    return {
        "touch_time": touch_time,
        "ret": round(ret, 6),
        "label": label,
        "pt_level": round(pt_level, 4),
        "sl_level": round(sl_level, 4),
    }


def get_bins(events: pd.DataFrame, close: pd.Series) -> pd.DataFrame:
    """
    Convert events DataFrame into labeled training rows.

    For pure labeling (no side): label ∈ {-1, 0, 1}
    For meta-labeling (side given): label ∈ {0, 1} — was the primary model correct?

    Args:
        events: output of get_events()
        close:  price series

    Returns:
        DataFrame with columns: ret, bin (label for training)
    """
    events_ = events.dropna(subset=["touch_time"])
    px = events_["touch_time"].map(close)
    out = pd.DataFrame(index=events_.index)
    out["ret"] = events_["ret"]
    out["label"] = events_["label"]

    # Meta-labeling: was primary side correct?
    if "side" in events_.columns:
        out["bin"] = np.where(out["label"] * events_["side"].values > 0, 1, 0)
    else:
        out["bin"] = out["label"]  # standard triple-barrier label

    return out[["ret", "bin"]]


# ─────────────────────────────────────────────
# 4. DROP RARE LABELS (optional balance step)
# ─────────────────────────────────────────────

def drop_labels(events: pd.DataFrame, min_pct: float = 0.05) -> pd.DataFrame:
    """
    Remove label classes that appear less than min_pct of the time.
    Prevents model from training on near-zero-sample classes.

    Args:
        events:  DataFrame with 'bin' column
        min_pct: minimum fraction for a class to be kept

    Returns:
        Filtered DataFrame
    """
    while True:
        counts = events["bin"].value_counts(normalize=True)
        rare = counts[counts < min_pct]
        if rare.empty:
            break
        events = events[~events["bin"].isin(rare.index)]
    return events


# ─────────────────────────────────────────────
# 5. HIGH-LEVEL CONVENIENCE: LABEL FROM DF
# ─────────────────────────────────────────────

def build_triple_barrier_labels(
    df: pd.DataFrame,
    pt_mult: float = 2.0,
    sl_mult: float = 1.0,
    vol_span: int = 20,
    num_days: int = 5,
    min_ret: float = 0.001,
    side: Optional[pd.Series] = None,
) -> pd.DataFrame:
    """
    End-to-end: take a raw OHLCV DataFrame, return labeled rows.

    This is the drop-in replacement for the naive label generation in ensemble.py:

        BEFORE (naive):
            future_ret = df["Close"].pct_change(FORWARD_DAYS).shift(-FORWARD_DAYS)
            labels[future_ret >  thresh] = 1
            labels[future_ret < -thresh] = 0

        AFTER (triple barrier):
            labeled = build_triple_barrier_labels(df, pt_mult=2.0, sl_mult=1.0)
            labels = labeled["bin"]

    Args:
        df:       OHLCV DataFrame with at minimum a 'Close' column
        pt_mult:  profit target width (multiples of daily vol)
        sl_mult:  stop loss width (multiples of daily vol)
        vol_span: EWM span for daily vol
        num_days: vertical barrier (max holding period in days)
        min_ret:  minimum daily vol for event to be included
        side:     optional primary model side series for meta-labeling

    Returns:
        DataFrame with columns: ret, bin
        Index aligns with df index — can be merged back with features directly.
    """
    close = df["Close"]
    if isinstance(close.index, pd.RangeIndex):
        # Give it a datetime index if not already (needed for time arithmetic)
        close = close.copy()
        close.index = pd.date_range(end=pd.Timestamp.today(), periods=len(close), freq="B")
        if side is not None:
            side = side.copy()
            side.index = close.index

    # Step 1: daily vol
    daily_vol = get_daily_vol(close, span=vol_span)

    # Step 2: use every row as a potential event
    t_events = close.index

    # Step 3: vertical barrier
    t1 = get_vertical_barrier(close, t_events, num_days=num_days)

    # Step 4: get events (first barrier touch per row)
    events = get_events(
        close=close,
        t_events=t_events,
        pt_sl=(pt_mult, sl_mult),
        target=daily_vol,
        min_ret=min_ret,
        t1=t1,
        side=side,
    )

    # Step 5: label
    labeled = get_bins(events, close)

    return labeled


# ─────────────────────────────────────────────
# 6. META-LABEL WRAPPER
# ─────────────────────────────────────────────

def build_meta_labels(
    df: pd.DataFrame,
    primary_side: pd.Series,
    pt_mult: float = 2.0,
    sl_mult: float = 1.0,
    num_days: int = 5,
) -> pd.DataFrame:
    """
    Build meta-labels given a primary model's direction predictions.

    Use this to train a secondary model that predicts:
        "Is the primary model correct on this trade?"

    The secondary model output (0/1 probability) is used as a
    confidence filter — only take trades where secondary_prob > threshold.

    Args:
        df:           OHLCV DataFrame
        primary_side: pd.Series with values +1 (BUY) or -1 (SELL)
                      aligned with df.index
        pt_mult:      profit target multiplier
        sl_mult:      stop loss multiplier
        num_days:     max holding period

    Returns:
        DataFrame with columns: ret, bin
        bin = 1 means "primary model was correct, take this trade"
        bin = 0 means "primary model was wrong, skip this trade"
    """
    return build_triple_barrier_labels(
        df=df,
        pt_mult=pt_mult,
        sl_mult=sl_mult,
        num_days=num_days,
        side=primary_side,
    )
