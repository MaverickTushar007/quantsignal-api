import requests
import logging

log = logging.getLogger(__name__)

TELEGRAM_TOKEN = "8611494119:AAGxk4nkCz590YEaJt_lfF83ZRX0WhRqTbM"
CHAT_ID = "776559643"

def send_telegram(msg: str) -> bool:
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        res = requests.post(url, json={
            "chat_id": CHAT_ID,
            "text": msg,
            "parse_mode": "HTML",
        }, timeout=5)
        return res.ok
    except Exception as e:
        log.warning(f"[telegram] failed: {e}")
        return False

def format_signal_alert(sig: dict) -> str:
    direction = sig.get("direction", "")
    symbol = sig.get("symbol", "")
    prob = sig.get("probability", 0)
    confluence = sig.get("confluence_score", "—")
    regime = sig.get("regime", "unknown")
    entry = sig.get("current_price", 0)
    tp = sig.get("take_profit", 0)
    sl = sig.get("stop_loss", 0)
    kelly = sig.get("kelly_size", 0)
    reasoning = sig.get("reasoning", "")

    emoji = "BUY 🟢" if direction == "BUY" else "SELL 🔴" if direction == "SELL" else "HOLD 🟡"
    regime_emoji = "Bull" if regime == "bull" else "Bear" if regime == "bear" else "Ranging"

    msg = f"""<b>{emoji} — {symbol}</b>

Probability: {(prob*100):.1f}%
Confluence: {confluence}
Regime: {regime_emoji}
Kelly Size: {kelly}%

Entry: {entry:,.2f}
Take Profit: {tp:,.2f}
Stop Loss: {sl:,.2f}"""

    if reasoning:
        msg += f"\n\nAI: {reasoning[:200]}..."

    msg += "\n\n<i>Educational only — not financial advice</i>"
    return msg
