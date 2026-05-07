"""
signal/confluence_verifier.py
Hard verification layer — treats confluence as a first-class object.
Checks news sentiment, macro alignment, liquidity, and directional coherence.
"""
import logging
import random
from datetime import datetime, timezone

log = logging.getLogger(__name__)

def verify_cache(cache: dict, sample_size: int = 40) -> dict:
    symbols = list(cache.keys())
    sample = random.sample(symbols, min(sample_size, len(symbols)))
    
    results = []
    for sym in sample:
        sig = cache[sym]
        check = verify_signal(sym, sig)
        results.append(check)
    
    scores = [r["score"] for r in results]
    flagged = [r for r in results if r["flagged"]]
    conflicts = [r for r in results if r["has_conflict"]]
    
    coverage = [r for r in results if r["has_coverage"]]
    
    return {
        "sampled": len(results),
        "avg_score": round(sum(scores) / len(scores), 3) if scores else 0,
        "flagged_symbols": [r["symbol"] for r in flagged],
        "conflict_density": round(len(conflicts) / len(results), 3) if results else 0,
        "coverage_pct": round(len(coverage) / len(results) * 100, 1) if results else 0,
        "details": results[:10],  # first 10 for logging
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def verify_signal(symbol: str, sig: dict) -> dict:
    direction = sig.get("direction", "HOLD")
    prob = sig.get("probability", 0)
    confluence = sig.get("confluence", [])
    news = sig.get("news", [])
    asset_type = sig.get("type", "")
    energy = sig.get("energy_state", "unknown")
    
    issues = []
    score = 1.0
    
    # 1. Directional coherence: exhausted energy + BUY = conflict
    if energy == "exhausted" and direction == "BUY":
        issues.append("exhausted_energy_buy_conflict")
        score -= 0.3
    if energy == "coiled" and direction in ("BUY", "SELL") and prob < 0.60:
        issues.append("coiled_low_confidence")
        score -= 0.1
    
    # 2. News sentiment vs direction
    bearish_news = sum(1 for n in news if n.get("sentiment") == "BEARISH")
    bullish_news = sum(1 for n in news if n.get("sentiment") == "BULLISH")
    if direction == "BUY" and bearish_news > bullish_news and bearish_news >= 2:
        issues.append("bearish_news_vs_buy_signal")
        score -= 0.2
    if direction == "SELL" and bullish_news > bearish_news and bullish_news >= 2:
        issues.append("bullish_news_vs_sell_signal")
        score -= 0.2
    
    # 3. Confluence coverage check
    bull_count = sum(1 for c in confluence if c.get("signal") == "BULLISH")
    bear_count = sum(1 for c in confluence if c.get("signal") == "BEARISH")
    has_coverage = len(confluence) >= 7
    
    if direction == "BUY" and bull_count < 4:
        issues.append(f"weak_bullish_confluence_{bull_count}_of_9")
        score -= 0.25
    if direction == "SELL" and bear_count < 4:
        issues.append(f"weak_bearish_confluence_{bear_count}_of_9")
        score -= 0.25
    
    # 4. TP/SL sanity
    price = sig.get("current_price", 0)
    tp = sig.get("take_profit", 0)
    sl = sig.get("stop_loss", 0)
    if price > 0 and tp > 0 and sl > 0:
        if direction == "BUY" and (tp <= price or sl >= price):
            issues.append("inverted_tp_sl_buy")
            score -= 0.4
        if direction == "SELL" and (tp >= price or sl <= price):
            issues.append("inverted_tp_sl_sell")
            score -= 0.4
    
    # 5. Liquidity domain check (crypto must have OI data ideally)
    if asset_type == "CRYPTO" and not sig.get("funding_rate") and not sig.get("open_interest"):
        issues.append("crypto_missing_liquidity_data")
        score -= 0.05
    
    score = round(max(0.0, min(1.0, score)), 3)
    
    return {
        "symbol": symbol,
        "direction": direction,
        "probability": prob,
        "score": score,
        "issues": issues,
        "flagged": score < 0.6 or len(issues) >= 2,
        "has_conflict": len(issues) > 0,
        "has_coverage": has_coverage,
        "bull_count": bull_count,
        "bear_count": bear_count,
    }
