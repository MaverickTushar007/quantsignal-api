"""
api/routes/billing.py — Razorpay Subscriptions billing layer.
"""
import os, hmac, hashlib, logging
from datetime import datetime, timezone
from fastapi import APIRouter, Request, Header, HTTPException
from typing import Optional

router = APIRouter()
log    = logging.getLogger(__name__)

RAZORPAY_KEY_ID           = os.environ.get("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET       = os.environ.get("RAZORPAY_KEY_SECRET", "")
RAZORPAY_WEBHOOK_SECRET   = os.environ.get("RAZORPAY_WEBHOOK_SECRET", "")
RZP_PRO_PLAN_ID           = os.environ.get("RAZORPAY_PRO_PLAN_ID", "")
RZP_INSTITUTIONAL_PLAN_ID = os.environ.get("RAZORPAY_INSTITUTIONAL_PLAN_ID", "")

def _plan_tier(plan_id):
    return {RZP_PRO_PLAN_ID: "pro", RZP_INSTITUTIONAL_PLAN_ID: "institutional"}.get(plan_id, "pro")

def _sb():
    from supabase import create_client
    return create_client(
        os.environ.get("SUPABASE_URL", ""),
        os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_ANON_KEY", "")
    )

def _rzp():
    import razorpay
    return razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))

def _upsert(user_id, tier, status, rzp_sub_id, email="", plan_id=""):
    try:
        _sb().table("user_subscriptions").upsert({
            "user_id": user_id, "tier": tier, "status": status,
            "ls_id": rzp_sub_id, "email": email, "variant_id": plan_id,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
    except Exception as e:
        log.error(f"[billing] upsert failed: {e}")

@router.post("/billing/webhook", tags=["billing"])
async def razorpay_webhook(request: Request):
    body      = await request.body()
    signature = request.headers.get("x-razorpay-signature", "")
    if RAZORPAY_WEBHOOK_SECRET:
        expected = hmac.new(RAZORPAY_WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise HTTPException(status_code=401, detail="Invalid webhook signature")
    import json
    payload = json.loads(body)
    event   = payload.get("event", "")
    entity  = payload.get("payload", {}).get("subscription", {}).get("entity", {})
    rzp_sub_id = entity.get("id", "")
    plan_id    = entity.get("plan_id", "")
    notes      = entity.get("notes", {})
    user_id    = notes.get("user_id", "") if isinstance(notes, dict) else ""
    email      = entity.get("email", "")
    tier       = _plan_tier(plan_id)
    log.info(f"[rzp webhook] event={event} user={user_id} tier={tier}")
    if not user_id:
        return {"status": "ignored", "reason": "no user_id"}
    if event in ("subscription.activated", "subscription.charged"):
        _upsert(user_id, tier, "active", rzp_sub_id, email, plan_id)
    elif event in ("subscription.cancelled", "subscription.completed"):
        _upsert(user_id, "free", "cancelled", rzp_sub_id, email, plan_id)
    elif event == "subscription.updated":
        new_plan = entity.get("plan_id", plan_id)
        _upsert(user_id, _plan_tier(new_plan), "active", rzp_sub_id, email, new_plan)
    elif event == "payment.failed":
        _upsert(user_id, tier, "payment_failed", rzp_sub_id, email, plan_id)
    return {"status": "ok", "event": event}

@router.post("/billing/create-subscription", tags=["billing"])
def create_subscription(body: dict = {}, x_user_id: Optional[str] = Header(None)):
    user_id = x_user_id or "anonymous"
    tier    = body.get("tier", "pro")
    email   = body.get("email", "")
    plan_id = RZP_PRO_PLAN_ID if tier == "pro" else RZP_INSTITUTIONAL_PLAN_ID
    if not plan_id:
        raise HTTPException(status_code=500, detail="Plan ID not configured")
    try:
        sub = _rzp().subscription.create({
            "plan_id": plan_id, "total_count": 12, "quantity": 1,
            "customer_notify": 1,
            "notes": {"user_id": user_id, "email": email, "tier": tier}
        })
        return {
            "subscription_id": sub["id"],
            "key_id":          RAZORPAY_KEY_ID,
            "tier":            tier,
            "amount":          99900 if tier == "pro" else 299900,
            "currency":        "INR",
        }
    except Exception as e:
        log.error(f"[billing] create subscription failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/billing/status", tags=["billing"])
def get_billing_status(x_user_id: Optional[str] = Header(None)):
    from app.domain.billing.middleware import get_user_tier
    from app.domain.billing.usage import get_usage
    from app.domain.billing.plans import get_plan
    user_id = x_user_id or "anonymous"
    tier    = get_user_tier(user_id)
    plan    = get_plan(tier)
    usage   = get_usage(user_id)
    return {
        "user_id": user_id, "tier": tier, "plan": plan,
        "usage": {
            "signals": {"used": usage.get("signals", 0), "limit": plan["signals_per_day"]},
            "perseus": {"used": usage.get("perseus", 0), "limit": plan["perseus_per_day"]},
        },
    }

@router.get("/billing/plans", tags=["billing"])
def list_plans():
    from app.domain.billing.plans import PLANS
    return {"plans": PLANS}
