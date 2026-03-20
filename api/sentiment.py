"""
api/sentiment.py
Live market sentiment endpoint — Fear & Greed, positioning, funding rates.
"""
from fastapi import APIRouter
router = APIRouter()

@router.get("/sentiment/debug", tags=["sentiment"])
def debug_sentiment():
    import requests
    results = {}
    try:
        r = requests.get("https://api.bybit.com/v5/market/account-ratio?category=linear&symbol=BTCUSDT&period=1d&limit=1", timeout=5)
        results["bybit_ls_status"] = r.status_code
        results["bybit_ls_body"] = r.text[:200]
    except Exception as e:
        results["bybit_ls_error"] = str(e)
    try:
        r2 = requests.get("https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd", timeout=5)
        results["coingecko_status"] = r2.status_code
        results["coingecko_body"] = r2.text[:100]
    except Exception as e:
        results["coingecko_error"] = str(e)
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
