"""
data/macro.py
Fetches macroeconomic regime features from FRED API.
Used as additional features in the ML ensemble.
Cached for 24 hours to avoid rate limits.
"""
import os
import json
import time
from pathlib import Path
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

CACHE_PATH = Path("data/macro_cache.json")
CACHE_TTL = 86400  # 24 hours

FRED_SERIES = {
    "fed_funds_rate": "FEDFUNDS",
    "cpi_yoy": "CPIAUCSL",
    "unemployment": "UNRATE",
    "yield_spread_10y2y": "T10Y2Y",
    "vix": "VIXCLS",
    "dxy": "DTWEXBGS",
}

def _load_cache():
    if CACHE_PATH.exists():
        try:
            cache = json.loads(CACHE_PATH.read_text())
            if time.time() - cache.get("timestamp", 0) < CACHE_TTL:
                return cache.get("data")
        except Exception:
            pass
    return None

def _save_cache(data: dict):
    CACHE_PATH.write_text(json.dumps({
        "timestamp": time.time(),
        "data": data
    }))

def get_macro_features() -> dict:
    """
    Returns dict of macro features for ML model.
    Falls back to neutral values if FRED unavailable.
    """
    cached = _load_cache()
    if cached:
        return cached

    try:
        from fredapi import Fred
        fred = Fred(api_key=os.getenv("FRED_API_KEY"))

        data = {}
        for feature_name, series_id in FRED_SERIES.items():
            try:
                series = fred.get_series(series_id, observation_start="2020-01-01")
                series = series.dropna()
                latest = float(series.iloc[-1])
                prev = float(series.iloc[-2])
                # CPI needs YoY calculation (12 periods back)
                if feature_name == "cpi_yoy":
                    prev_year = float(series.iloc[-13]) if len(series) >= 13 else prev
                    latest = round((latest - prev_year) / prev_year * 100, 2)
                    prev_val = float(series.iloc[-2])
                    prev_year2 = float(series.iloc[-14]) if len(series) >= 14 else prev_val
                    prev_yoy = round((prev_val - prev_year2) / prev_year2 * 100, 2)
                    data[feature_name] = latest
                    data[f"{feature_name}_change"] = latest - prev_yoy
                else:
                    data[feature_name] = latest
                    data[f"{feature_name}_change"] = latest - prev
            except Exception as e:
                print(f"FRED fetch failed for {series_id}: {e}")
                data[feature_name] = 0.0
                data[f"{feature_name}_change"] = 0.0

        # Derived regime features
        data["rate_hike_regime"] = 1.0 if data.get("fed_funds_rate_change", 0) > 0 else 0.0
        data["inflation_high"] = 1.0 if data.get("cpi_yoy", 0) > 4.0 else 0.0
        data["recession_signal"] = 1.0 if data.get("yield_spread_10y2y", 1) < 0 else 0.0
        data["high_fear"] = 1.0 if data.get("vix", 20) > 25 else 0.0
        data["dollar_strong"] = 1.0 if data.get("dxy_change", 0) > 0 else 0.0

        _save_cache(data)
        print(f"FRED macro features loaded: {list(data.keys())}")
        return data

    except Exception as e:
        print(f"FRED macro fetch failed entirely: {e}")
        return {
            "fed_funds_rate": 5.33,
            "fed_funds_rate_change": 0.0,
            "cpi_yoy": 3.2,
            "cpi_yoy_change": 0.0,
            "unemployment": 3.9,
            "unemployment_change": 0.0,
            "yield_spread_10y2y": 0.2,
            "yield_spread_10y2y_change": 0.0,
            "vix": 18.0,
            "vix_change": 0.0,
            "dxy": 104.0,
            "dxy_change": 0.0,
            "rate_hike_regime": 0.0,
            "inflation_high": 0.0,
            "recession_signal": 0.0,
            "high_fear": 0.0,
            "dollar_strong": 0.0,
        }
