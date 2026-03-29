"""
Proactive Reasoning Engine — runs after each cache rebuild.
Detects notable events and pushes insights to subscribed users.
Events: regime changes, high-confidence signals, circuit breaker triggers.
"""
import logging
log = logging.getLogger(__name__)

def run_proactive_engine(cache: dict, old_cache: dict) -> dict:
    """
    Compare new cache vs old cache, generate insights, push to users.
    Returns summary of what was pushed.
    """
    insights = []

    try:
        # 1. Detect regime changes
        for symbol, sig in cache.items():
            old_sig = old_cache.get(symbol, {})
            new_regime = sig.get("regime")
            old_regime = old_sig.get("regime")
            if new_regime and old_regime and new_regime != old_regime:
                insights.append({
                    "type": "regime_change",
                    "symbol": symbol,
                    "title": f"⚡ {symbol} Regime Change",
                    "body": f"{old_regime.upper()} → {new_regime.upper()} | {sig.get('direction','HOLD')} signal at {sig.get('probability', 0):.0%} confidence",
                    "url": f"/dashboard?symbol={symbol}",
                    "tag": f"regime_{symbol}",
                    "priority": "high",
                })

        # 2. Detect high-confidence signals (≥75%)
        for symbol, sig in cache.items():
            prob = sig.get("probability", 0)
            direction = sig.get("direction", "HOLD")
            old_prob = old_cache.get(symbol, {}).get("probability", 0)
            # Only push if newly crossed 75% threshold
            if prob >= 0.75 and old_prob < 0.75 and direction in ("BUY", "SELL"):
                insights.append({
                    "type": "high_confidence",
                    "symbol": symbol,
                    "title": f"🎯 {symbol} High Confidence Signal",
                    "body": f"{direction} at {prob:.0%} confidence | {sig.get('confluence_score', '?')} confluence",
                    "url": f"/dashboard?symbol={symbol}",
                    "tag": f"signal_{symbol}",
                    "priority": "high",
                })

        # 3. Circuit breaker state change
        try:
            from app.domain.core.circuit_breaker import check_circuit_breaker
            cb = check_circuit_breaker()
            old_cb_active = old_cache.get("__circuit_breaker__", {}).get("active", False)
            if cb.get("active") and not old_cb_active:
                insights.append({
                    "type": "circuit_breaker",
                    "symbol": None,
                    "title": "🔴 Circuit Breaker Activated",
                    "body": cb.get("reason", "System paused due to poor signal quality"),
                    "url": "/dashboard",
                    "tag": "circuit_breaker",
                    "priority": "critical",
                })
        except Exception:
            pass

        log.info(f"[proactive] {len(insights)} insights generated")

        if not insights:
            return {"pushed": 0, "insights": []}

        # Push all high-priority insights
        pushed = _push_insights(insights)
        return {"pushed": pushed, "insights": [i["type"] for i in insights]}

    except Exception as e:
        log.error(f"[proactive] failed: {e}")
        return {"error": str(e), "pushed": 0}


def _push_insights(insights: list) -> int:
    """Send push notifications for each insight."""
    try:
        from app.domain.alerts.webpush import send_push_to_all
        pushed = 0
        for insight in insights:
            try:
                send_push_to_all(
                    title=insight["title"],
                    body=insight["body"],
                    url=insight.get("url", "/dashboard"),
                )
                pushed += 1
                log.info(f"[proactive] pushed: {insight['title']}")
            except Exception as e:
                log.debug(f"[proactive] push failed for {insight.get('symbol')}: {e}")
        return pushed
    except Exception as e:
        log.debug(f"[proactive] _push_insights failed: {e}")
        return 0
