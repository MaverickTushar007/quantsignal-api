"""
api/liquidity.py
Real-time liquidity levels — OI change, funding trend, liquidation clusters.
Polls OKX live. No cache — always fresh.
"""
from fastapi import APIRouter, HTTPException
import requests

router = APIRouter()

OKX_INST_MAP = {
    "BTC-USD": "BTC-USDT-SWAP", "ETH-USD": "ETH-USDT-SWAP",
    "SOL-USD": "SOL-USDT-SWAP", "BNB-USD": "BNB-USDT-SWAP",
    "XRP-USD": "XRP-USDT-SWAP", "DOGE-USD": "DOGE-USDT-SWAP",
    "ADA-USD": "ADA-USDT-SWAP", "AVAX-USD": "AVAX-USDT-SWAP",
    "DOT-USD": "DOT-USDT-SWAP", "LINK-USD": "LINK-USDT-SWAP",
    "LTC-USD": "LTC-USDT-SWAP", "ATOM-USD": "ATOM-USDT-SWAP",
    "NEAR-USD": "NEAR-USDT-SWAP", "OP-USD": "OP-USDT-SWAP",
    "INJ-USD": "INJ-USDT-SWAP",
}

@router.get("/liquidity/{symbol}", tags=["liquidity"])
def get_liquidity_levels(symbol: str):
    inst_id = OKX_INST_MAP.get(symbol.upper())
    if not inst_id:
        raise HTTPException(status_code=404, detail=f"Symbol {symbol} not supported")

    try:
        # 1. Current price from OKX ticker
        ticker_url = f"https://www.okx.com/api/v5/market/ticker?instId={inst_id}"
        ticker_resp = requests.get(ticker_url, timeout=8).json()
        ticker_data = ticker_resp.get("data", [])
        if not ticker_data:
            raise ValueError("Empty ticker response")
        current_price = float(ticker_data[0]["last"])

        # 2. Current Open Interest
        oi_url = f"https://www.okx.com/api/v5/public/open-interest?instType=SWAP&instId={inst_id}"
        oi_resp = requests.get(oi_url, timeout=8).json()
        oi_data = oi_resp.get("data", [])
        current_oi = float(oi_data[0].get("oiCcy", 0)) if oi_data else 0.0

        # 3. OI history (last 24 candles of 1H = 24h ago)
        oi_hist_url = f"https://www.okx.com/api/v5/rubik/stat/contracts/open-interest-volume?instId={inst_id}&period=1H&limit=25"
        oi_hist_resp = requests.get(oi_hist_url, timeout=8).json()
        oi_hist = oi_hist_resp.get("data", [])
        oi_24h_ago = float(oi_hist[-1][1]) if len(oi_hist) >= 24 else current_oi
        oi_change_pct = round(((current_oi - oi_24h_ago) / oi_24h_ago) * 100, 2) if oi_24h_ago else 0.0

        # 4. Funding rate
        funding_url = f"https://www.okx.com/api/v5/public/funding-rate?instId={inst_id}"
        funding_resp = requests.get(funding_url, timeout=8).json()
        funding_data = funding_resp.get("data", [])
        funding_rate = float(funding_data[0]["fundingRate"]) * 100 if funding_data else 0.0
        next_funding_raw = funding_data[0].get("nextFundingRate", "0") or "0"
        next_funding = float(next_funding_raw) * 100 if funding_data else 0.0

        # Funding trend
        if funding_rate > 0.03:
            funding_trend = "EXTREMELY_LONG"
            funding_color = "#ff4466"
        elif funding_rate > 0.01:
            funding_trend = "LEANING_LONG"
            funding_color = "#ffd700"
        elif funding_rate < -0.03:
            funding_trend = "EXTREMELY_SHORT"
            funding_color = "#00ff88"
        elif funding_rate < -0.01:
            funding_trend = "LEANING_SHORT"
            funding_color = "#00aaff"
        else:
            funding_trend = "NEUTRAL"
            funding_color = "rgba(255,255,255,0.4)"

        # 5. Long/Short ratio
        ls_url = f"https://www.okx.com/api/v5/rubik/stat/contracts/long-short-account-ratio-contract?instId={inst_id}&period=1D&limit=1"
        ls_resp = requests.get(ls_url, timeout=8).json()
        ls_data = ls_resp.get("data", [])
        ls_ratio = float(ls_data[0][1]) if ls_data else 1.0
        long_ratio = round(ls_ratio / (1 + ls_ratio) * 100, 1)
        short_ratio = round(100 - long_ratio, 1)

        # 6. Liquidation clusters — approximate from price levels
        # Logic: leveraged positions concentrate at round numbers and ±2%, ±5%, ±10%
        # Longs liquidate BELOW current price, shorts liquidate ABOVE
        clusters_above = []
        clusters_below = []

        for pct, weight in [(2, "LIGHT"), (5, "MODERATE"), (8, "HEAVY"), (12, "MAJOR")]:
            price_above = round(current_price * (1 + pct / 100), 2)
            price_below = round(current_price * (1 - pct / 100), 2)

            # Weight by OI — higher OI = bigger clusters
            oi_weight = "HIGH" if current_oi > 50000 else "MEDIUM" if current_oi > 20000 else "LOW"

            clusters_above.append({
                "price": price_above,
                "distance_pct": pct,
                "weight": weight,
                "label": f"+{pct}% short squeeze zone",
            })
            clusters_below.append({
                "price": price_below,
                "distance_pct": pct,
                "weight": weight,
                "label": f"-{pct}% long liquidation zone",
            })

        # 7. Market bias summary
        if long_ratio > 65 and funding_rate > 0.01:
            bias = "OVERLEVERAGED_LONG"
            bias_desc = "Market heavily long + positive funding — short squeeze risk is low, long liquidation cascade risk is HIGH"
            bias_color = "#ff4466"
        elif long_ratio < 35 and funding_rate < -0.01:
            bias = "OVERLEVERAGED_SHORT"
            bias_desc = "Market heavily short + negative funding — long squeeze risk is HIGH, potential for rapid upside"
            bias_color = "#00ff88"
        elif oi_change_pct > 5:
            bias = "LEVERAGE_BUILDING"
            bias_desc = "Open interest rising fast — new leveraged positions entering, volatility likely incoming"
            bias_color = "#ffd700"
        elif oi_change_pct < -5:
            bias = "DELEVERAGING"
            bias_desc = "Open interest dropping — positions being closed, volatility cooling down"
            bias_color = "#00aaff"
        else:
            bias = "BALANCED"
            bias_desc = "Leverage is balanced — no extreme positioning detected"
            bias_color = "rgba(255,255,255,0.4)"

        return {
            "symbol": symbol,
            "current_price": current_price,
            "open_interest": round(current_oi, 2),
            "oi_change_24h_pct": oi_change_pct,
            "funding_rate": round(funding_rate, 6),
            "next_funding_rate": round(next_funding, 6),
            "funding_trend": funding_trend,
            "funding_color": funding_color,
            "long_ratio": long_ratio,
            "short_ratio": short_ratio,
            "clusters_above": clusters_above[:3],
            "clusters_below": clusters_below[:3],
            "bias": bias,
            "bias_desc": bias_desc,
            "bias_color": bias_color,
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
