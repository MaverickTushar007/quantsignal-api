"""
Perseus Watcher — Sprint 5
Scans top assets every 15 minutes.
Fires Telegram alerts when high-confidence signal detected.
Perseus watches markets so users don't have to.
"""
import logging
from datetime import datetime, timezone

log = logging.getLogger(__name__)

TOP_ASSETS = [
    "BTC-USD", "ETH-USD", "SOL-USD", "BNB-USD",
    "RELIANCE.NS", "TCS.NS", "INFY.NS", "HDFCBANK.NS",
    "AAPL", "NVDA", "MSFT",
    "GC=F",  # Gold
]

# Minimum confidence to fire an alert (avoid noise)
MIN_CONFIDENCE = 0.40  # TEMP: lowered for testing
MIN_CONFLUENCE = 1  # TEMP


def _parse_confluence(score_str: str) -> int:
    """Parse '6/9 bullish' → 6"""
    try:
        return int(str(score_str).split("/")[0])
    except Exception:
        return 0


def scan_and_alert():
    """
    Scan all top assets. Fire alert if high-confidence signal found.
    Called every 15 minutes by APScheduler.
    """
    log.info(f"[Perseus Watcher] Scanning {len(TOP_ASSETS)} assets at {datetime.now(timezone.utc).isoformat()}")
    alerts_fired = 0

    for symbol in TOP_ASSETS:
        try:
            from app.domain.signal.service import generate_signal
            sig = generate_signal(symbol, include_reasoning=False)

            if not sig:
                continue

            prob = float(sig.get("probability", 0))
            confluence_bulls = _parse_confluence(sig.get("confluence_score", "0/9"))
            direction = sig.get("direction", "HOLD")

            # Skip HOLD and low-confidence signals
            if direction == "HOLD":
                continue
            if prob < MIN_CONFIDENCE:
                continue
            if confluence_bulls < MIN_CONFLUENCE:
                continue

            log.info(f"[Perseus Watcher] HIGH CONFIDENCE: {symbol} {direction} {prob*100:.0f}% — alerting")

            # Generate Perseus reasoning for the alert
            try:
                from app.domain.reasoning.service import get_reasoning
                reasoning = get_reasoning(
                    ticker=symbol,
                    name=sig.get("name", symbol),
                    direction=direction,
                    probability=prob,
                    confluence_bulls=confluence_bulls,
                    top_features=sig.get("top_features", []),
                    news_headlines=[],
                    current_price=sig.get("current_price", 0),
                    take_profit=sig.get("take_profit", 0),
                    stop_loss=sig.get("stop_loss", 0),
                    atr=sig.get("atr", 0),
                    volume_ratio=sig.get("volume_ratio", 1.0),
                    model_agreement=sig.get("model_agreement", 0),
                )
                sig["reasoning"] = reasoning
            except Exception as _re:
                log.warning(f"[Perseus Watcher] reasoning failed for {symbol}: {_re}")

            # Save signal to history
            try:
                from app.infrastructure.db.signal_history import save_signal
                sig["symbol"] = symbol
                save_signal(sig)
            except Exception:
                pass

            # Store embedding
            try:
                from app.infrastructure.db.signal_embeddings import store_embedding
                store_embedding(sig)
            except Exception:
                pass

            # Send alerts to all watchers
            _notify_watchers(symbol, sig)
            alerts_fired += 1

        except Exception as e:
            log.error(f"[Perseus Watcher] failed for {symbol}: {e}")

    log.info(f"[Perseus Watcher] Scan complete — {alerts_fired} alerts fired")
    return alerts_fired


def _notify_watchers(symbol: str, signal: dict):
    """
    Send Telegram alert to all Pro users watching this asset.
    Falls back to broadcast channel if no per-user watchlists.
    """
    from app.domain.alerts.telegram import send_telegram, format_signal_alert

    msg = format_signal_alert(signal)
    msg = f"⚡ <b>PERSEUS ALERT</b>\n\n" + msg

    # Per-user alerts (if watchlist table exists)
    users_alerted = 0
    try:
        from app.infrastructure.db.watchlist import get_watchers
        watchers = get_watchers(symbol)
        for watcher in watchers:
            chat_id = watcher.get("telegram_chat_id")
            if chat_id:
                _send_to_chat(chat_id, msg)
                users_alerted += 1
    except Exception:
        pass

    # Always also send to main channel
    send_telegram(msg)
    log.info(f"[Perseus Watcher] Alerted {users_alerted} users + main channel for {symbol}")


def _send_to_chat(chat_id: str, msg: str) -> bool:
    """Send message to a specific Telegram chat ID."""
    import requests
    import os
    token = os.getenv("TELEGRAM_BOT_TOKEN", "8611494119:AAGxk4nkCz590YEaJt_lfF83ZRX0WhRqTbM")
    try:
        res = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": msg, "parse_mode": "HTML"},
            timeout=5,
        )
        return res.ok
    except Exception:
        return False
