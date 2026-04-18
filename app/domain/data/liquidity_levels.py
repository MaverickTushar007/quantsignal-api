"""
domain/data/liquidity_levels.py
Standalone liquidity level fetch — avoids circular import with api/routes/liquidity.py
Used by signal generation to place TP/SL at liquidity-aware levels.
"""
import requests

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

def get_liquidity_clusters(symbol: str, current_price: float) -> dict:
    """
    Returns liquidation cluster levels for TP/SL snapping.
    For crypto: uses OKX live price + percentage bands.
    For stocks: returns None (no futures liquidation data).
    """
    if symbol not in OKX_INST_MAP:
        return None

    try:
        inst_id = OKX_INST_MAP[symbol]

        # Get long/short ratio to determine cluster weights
        ls_url = f"https://www.okx.com/api/v5/rubik/stat/contracts/long-short-account-ratio-contract?instId={inst_id}&period=1D&limit=1"
        ls_resp = requests.get(ls_url, timeout=6).json()
        ls_data = ls_resp.get("data", [])
        ls_ratio = float(ls_data[0][1]) if ls_data else 1.0
        long_ratio = ls_ratio / (1 + ls_ratio)

        # Cluster levels — shorts liquidate above, longs below
        clusters_above = [round(current_price * (1 + p/100), 2) for p in [2, 5, 8, 12]]
        clusters_below = [round(current_price * (1 - p/100), 2) for p in [2, 5, 8, 12]]

        return {
            "clusters_above": clusters_above,  # short squeeze zones
            "clusters_below": clusters_below,  # long liquidation zones
            "long_ratio": round(long_ratio, 4),
            "short_ratio": round(1 - long_ratio, 4),
        }
    except Exception:
        return None


def snap_to_liquidity(
    direction: str,
    current_price: float,
    raw_tp: float,
    raw_sl: float,
    clusters: dict,
) -> tuple:
    """
    Snaps TP/SL to liquidity-aware levels.

    BUY signal:
    - TP should be just BELOW the nearest short squeeze zone (not inside it)
    - SL should be just BELOW the nearest long liquidation zone (let longs flush before stopping out)

    SELL signal:
    - TP should be just ABOVE the nearest long liquidation zone (ride the cascade)
    - SL should be just ABOVE the nearest short squeeze zone

    Returns (adjusted_tp, adjusted_sl, snap_info)
    """
    if not clusters:
        return raw_tp, raw_sl, None

    above = clusters["clusters_above"]  # [+2%, +5%, +8%, +12%]
    below = clusters["clusters_below"]  # [-2%, -5%, -8%, -12%]
    snap_info = {}

    if direction == "BUY":
        # Find nearest cluster above that raw_tp is approaching
        # TP: place just below the first squeeze zone above current price
        tp_target = raw_tp
        for cluster in above:
            if raw_tp >= cluster * 0.98:  # within 2% of a squeeze zone
                tp_target = round(cluster * 0.995, 4)  # 0.5% below the zone
                snap_info["tp_snapped"] = f"TP moved below squeeze zone at {cluster}"
                break

        # SL: place just below nearest long liq zone below current price
        sl_target = raw_sl
        for cluster in reversed(below):
            if raw_sl <= cluster * 1.02:  # within 2% of a liq zone
                sl_target = round(cluster * 0.995, 4)  # just below the zone
                snap_info["sl_snapped"] = f"SL moved below long liq zone at {cluster}"
                break

        # Safety: TP must still be above entry, SL below
        tp_target = max(tp_target, current_price * 1.003)
        sl_target = min(sl_target, current_price * 0.997)

    else:  # SELL
        # TP: place just above nearest long liq zone below current price
        tp_target = raw_tp
        for cluster in below:
            if raw_tp <= cluster * 1.02:
                tp_target = round(cluster * 1.005, 4)  # just above the zone
                snap_info["tp_snapped"] = f"TP moved above long liq zone at {cluster}"
                break

        # SL: place just above nearest short squeeze zone above current
        sl_target = raw_sl
        for cluster in above:
            if raw_sl >= cluster * 0.98:
                sl_target = round(cluster * 1.005, 4)
                snap_info["sl_snapped"] = f"SL moved above short squeeze zone at {cluster}"
                break

        # Safety: TP must still be below entry, SL above
        tp_target = min(tp_target, current_price * 0.997)
        sl_target = max(sl_target, current_price * 1.003)

    return tp_target, sl_target, snap_info if snap_info else None
