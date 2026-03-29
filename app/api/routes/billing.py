"""
api/routes/billing.py
LemonSqueezy webhook + subscription management endpoints.
Webhook secret must be set in env: LEMONSQUEEZY_WEBHOOK_SECRET
"""
import os
import hmac
import hashlib
import logging
from datetime import datetime, timezone
from fastapi import APIRouter, Request, Header, HTTPException, Depends
from typing import Optional

router = APIRouter()
log    = logging.getLogger(__name__)

LS_WEBHOOK_SECRET = os.environ.get("LEMONSQUEEZY_WEBHOOK_SECRET", "")

# Map LemonSqueezy variant IDs → our tier names
# Fill these in after creating products in LS dashboard
VARIANT_TIER_MAP: dict[str, str] = {
    os.environ.get("LS_PRO_VARIANT_ID", ""):           "pro",
    os.environ.get("LS_INSTITUTIONAL_VARIANT_ID", ""): "institutional",
}


def _sb():
    from supabase import create_client
    return create_client(
        os.environ.get("SUPABASE_URL", ""),
        os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_ANON_KEY", "")
    )


@router.post("/billing/webhook", tags=["billing"])
async def lemonsqueezy_webhook(request: Request):
    """
    LemonSqueezy webhook endpoint.
    Handles: subscription_created, subscription_updated, subscription_cancelled
    """
    body      = await request.body()
    signature = request.headers.get("x-signature", "")

    # Verify webhook signature
    if LS_WEBHOOK_SECRET:
        expected = hmac.new(
            LS_WEBHOOK_SECRET.encode(),
            body,
            hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise HTTPException(status_code=401, detail="Invalid webhook signature")

    import json
    payload    = json.loads(body)
    event_name = payload.get("meta", {}).get("event_name", "")
    data       = payload.get("data", {})
    attrs      = data.get("attributes", {})

    log.info(f"[LS webhook] event={event_name}")

    # Extract user_id from custom data (set this when creating checkout)
    custom_data = attrs.get("first_subscription_item", {})
    user_id     = payload.get("meta", {}).get("custom_data", {}).get("user_id", "")
    variant_id  = str(attrs.get("variant_id", ""))
    tier        = VARIANT_TIER_MAP.get(variant_id, "pro")
    status      = attrs.get("status", "active")
    ls_id       = str(data.get("id", ""))
    email       = attrs.get("user_email", "")

    if not user_id:
        log.warning("[LS webhook] no user_id in custom_data")
        return {"status": "ignored", "reason": "no user_id"}

    if event_name in ("subscription_created", "subscription_updated"):
        _upsert_subscription(user_id, tier, status, ls_id, email, variant_id)
        log.info(f"[LS webhook] {user_id} → {tier} ({status})")

    elif event_name == "subscription_cancelled":
        _upsert_subscription(user_id, "free", "cancelled", ls_id, email, variant_id)
        log.info(f"[LS webhook] {user_id} cancelled → downgraded to free")

    elif event_name == "subscription_expired":
        _upsert_subscription(user_id, "free", "expired", ls_id, email, variant_id)

    return {"status": "ok", "event": event_name}


@router.get("/billing/status", tags=["billing"])
def get_billing_status(x_user_id: Optional[str] = Header(None)):
    """Get current subscription status + usage for a user."""
    from app.domain.billing.middleware import get_user_tier
    from app.domain.billing.usage import get_usage
    from app.domain.billing.plans import get_plan

    user_id = x_user_id or "anonymous"
    tier    = get_user_tier(user_id)
    plan    = get_plan(tier)
    usage   = get_usage(user_id)

    return {
        "user_id":  user_id,
        "tier":     tier,
        "plan":     plan,
        "usage":    {
            "signals": {
                "used":  usage.get("signals", 0),
                "limit": plan["signals_per_day"],
            },
            "perseus": {
                "used":  usage.get("perseus", 0),
                "limit": plan["perseus_per_day"],
            },
        },
    }


@router.get("/billing/plans", tags=["billing"])
def list_plans():
    """Return all available plans — used by frontend pricing page."""
    from app.domain.billing.plans import PLANS
    return {"plans": PLANS}


@router.post("/billing/checkout", tags=["billing"])
def create_checkout(x_user_id: Optional[str] = Header(None), body: dict = {}):
    """
    Generate a LemonSqueezy checkout URL for a given tier.
    Frontend redirects user to this URL to complete payment.
    """
    user_id    = x_user_id or "anonymous"
    tier       = body.get("tier", "pro")
    store_id   = os.environ.get("LS_STORE_ID", "")
    variant_id = os.environ.get(
        "LS_PRO_VARIANT_ID" if tier == "pro" else "LS_INSTITUTIONAL_VARIANT_ID", ""
    )

    if not variant_id:
        return {
            "error": "Variant ID not configured — set LS_PRO_VARIANT_ID in env",
            "setup_url": "https://app.lemonsqueezy.com"
        }

    checkout_url = (
        f"https://quantsignal.lemonsqueezy.com/checkout/buy/{variant_id}"
        f"?checkout[custom][user_id]={user_id}"
        f"&checkout[email]="
    )

    return {"checkout_url": checkout_url, "tier": tier}


def _upsert_subscription(user_id, tier, status, ls_id, email, variant_id):
    try:
        sb = _sb()
        sb.table("user_subscriptions").upsert({
            "user_id":    user_id,
            "tier":       tier,
            "status":     status,
            "ls_id":      ls_id,
            "email":      email,
            "variant_id": variant_id,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
    except Exception as e:
        log.error(f"[LS] upsert subscription failed: {e}")
