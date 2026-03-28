import json
import logging

log = logging.getLogger(__name__)

VAPID_PUBLIC = "BLyTAmlOorAbbCuSH67CuKW04Efp8Lr5RJgqrEobQBKQYM3UrrSi-PesbgyzTwl8mDdXKbaIIbEgTJOABEkCH6w="
VAPID_PRIVATE = """-----BEGIN EC PRIVATE KEY-----
MHcCAQEEIIicfEZSGzsrr4km+lDYF999bjrIjx4lhiHB3lhORQmhoAoGCCqGSM49
AwEHoUQDQgAEvJMCaU6isBtsK5IfrsK4pbTgR+nwuvlEmCqsShtAEpBgzdSutKL4
96xuDLNPCXyYN1cptoghsSBMk4AESQIfrA==
-----END EC PRIVATE KEY-----"""
VAPID_CLAIMS = {"sub": "mailto:tusharbhatt@example.com"}

_subscriptions: list[dict] = []

def add_subscription(sub: dict):
    endpoint = sub.get("endpoint")
    for existing in _subscriptions:
        if existing.get("endpoint") == endpoint:
            return
    _subscriptions.append(sub)
    log.info(f"[webpush] new subscription, total: {len(_subscriptions)}")

def remove_subscription(endpoint: str):
    global _subscriptions
    _subscriptions = [s for s in _subscriptions if s.get("endpoint") != endpoint]

def send_push_to_all(title: str, body: str, url: str = "/dashboard"):
    if not _subscriptions:
        return
    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        log.warning("[webpush] pywebpush not installed — skipping")
        return
    payload = json.dumps({"title": title, "body": body, "url": url})
    dead = []
    for sub in _subscriptions:
        try:
            webpush(
                subscription_info=sub,
                data=payload,
                vapid_private_key=VAPID_PRIVATE,
                vapid_claims=VAPID_CLAIMS,
            )
        except Exception as e:
            log.warning(f"[webpush] error: {e}")
            if "410" in str(e) or "404" in str(e):
                dead.append(sub.get("endpoint"))
    for d in dead:
        remove_subscription(d)

def format_push_alert(sig: dict) -> tuple[str, str]:
    direction = sig.get("direction", "")
    symbol = sig.get("symbol", "")
    prob = sig.get("probability", 0)
    tp = sig.get("take_profit", 0)
    sl = sig.get("stop_loss", 0)
    title = f"{direction} {symbol}"
    body = f"Prob: {prob*100:.0f}% | TP: {tp:,.0f} | SL: {sl:,.0f}"
    return title, body
