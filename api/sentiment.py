"""
api/sentiment.py
Live market sentiment endpoint — Fear & Greed, positioning, funding rates.
"""
from fastapi import APIRouter
router = APIRouter()

@router.get("/sentiment/clear-cache", tags=["sentiment"])
def clear_cache():
    import os
    cleared = []
    for f in ["data/positioning_cache.json", "data/funding_cache.json", "data/macro_cache.json", "data/fear_greed_cache.json"]:
        try:
            os.remove(f)
            cleared.append(f)
        except:
            pass
    return {"cleared": cleared}

@router.get("/sentiment/debug", tags=["sentiment"])
def debug_sentiment():
    import requests
    results = {}
    for name, url in {
        "coingecko_price": "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd",
        "coingecko_derivatives": "https://api.coingecko.com/api/v3/derivatives/exchanges/bitmex?include_tickers=unexpired",
        "deribit": "https://www.deribit.com/api/v2/public/get_funding_rate_value?instrument_name=BTC-PERPETUAL",
        "okx_funding": "https://www.okx.com/api/v5/public/funding-rate?instId=BTC-USDT-SWAP",
        "bitget": "https://api.bitget.com/api/v2/mix/market/symbol-leverage?symbol=BTCUSDT&productType=USDT-FUTURES",
    }.items():
        try:
            r = requests.get(url, timeout=5)
            results[name] = {"status": r.status_code, "body": r.text[:150]}
        except Exception as e:
            results[name] = {"error": str(e)}
    return results

@router.get("/sentiment/market", tags=["sentiment"])
def market_sentiment():
    try:
        from data.fear_greed import get_fear_greed
        from data.positioning import get_positioning
        from data.funding import get_funding_features
        from data.macro import get_macro_features

        fg = get_fear_greed()
        btc_pos = get_positioning("BTC-USD")
        eth_pos = get_positioning("ETH-USD")
        btc_funding = get_funding_features("BTC-USD")
        macro = get_macro_features()

        return {
            "fear_greed": {
                "score": fg.get("score"),
                "classification": fg.get("classification"),
                "prev_score": fg.get("prev_score"),
                "contrarian_signal": fg.get("contrarian_signal"),
            },
            "btc_positioning": {
                "long_ratio": btc_pos.get("long_ratio"),
                "short_ratio": btc_pos.get("short_ratio"),
                "long_short_ratio": btc_pos.get("long_short_ratio"),
                "open_interest": btc_pos.get("open_interest"),
                "crowded_long": btc_pos.get("crowded_long"),
                "crowded_short": btc_pos.get("crowded_short"),
            },
            "eth_positioning": {
                "long_ratio": eth_pos.get("long_ratio"),
                "long_short_ratio": eth_pos.get("long_short_ratio"),
                "crowded_long": eth_pos.get("crowded_long"),
            },
            "btc_funding": {
                "rate": btc_funding.get("funding_rate"),
                "signal": btc_funding.get("funding_signal"),
            },
            "macro": {
                "vix": macro.get("vix"),
                "high_fear": macro.get("high_fear"),
                "recession_signal": macro.get("recession_signal"),
                "fed_funds_rate": macro.get("fed_funds_rate"),
                "cpi_yoy": macro.get("cpi_yoy"),
            }
        }
    except Exception as e:
        return {"error": str(e)}
