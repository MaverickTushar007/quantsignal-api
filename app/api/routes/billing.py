"""
api/routes/billing.py  —  Razorpay Subscriptions billing layer
"""
import os, hmac, hashlib, logging, json
from datetime import datetime, timezone
from fastapi import APIRouter, Request, Header, HTTPException
from typing import Optional

router = APIRouter()
log    = logging.getLogger(__name__)

RZP_KEY_ID     = os.environ.get("RAZORPAY_KEY_ID", "")
RZP_KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET", "")
RZP_WEBHOOK_SECRET = os.environ.get("RAZORPAY_WEBHOOK_SECRET", "")

PLAN_MAP = {
    "pro":           os.environ.get("RAZORPAY_PRO_PLAN_ID", ""),
    "institutional": os.environ.get("RAZORPAY_INSTITUTIONAL_PLAN_ID", ""),
}


def _rzp():
    import razorpay
    return razorpay.Client(auth=(RZP_KEY_ID, RZP_KEY_SECRET))


def _sb():
    from supabase import create_client
    return create_client(
        os.environ.get("SUPABASE_URL", ""),
        os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_ANON_KEY", "")
    )


def _upsert_subscription(user_id, tier, status, rzp_sub_id, email=""):
    try:
        _sb().table("user_subscriptions").upsert({
            "user_id":    user_id,
            "tier":       tier,
            "status":     status,
            "ls_id":      rzp_sub_id,
            "email":      email,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
    except Exception as e:
        log.error(f"[billing] upsert failed: {e}")


@router.post("/billing/webhook", tags=["billing"])
async def razorpay_webhook(request: Request):
    body      = await request.body()
    signature = request.headers.get("x-razorpay-signature", "")

    if RZP_WEBHOOK_SECRET:
        expected = hmac.new(
            RZP_WEBHOOK_SECRET.encode(),
            body,
            hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise HTTPException(status_code=401, detail="Invalid webhook signature")

    payload    = json.loads(body)
    event      = payload.get("event", "")
    entity     = payload.get("payload", {}).get("subscription", {}).get("entity", {})
    rzp_sub_id = entity.get("id", "")
    plan_id    = entity.get("plan_id", "")
    notes      = entity.get("notes", {})
    user_id    = notes.get("user_id", "")
    email      = entity.get("email", "")

    # Reverse-map plan_id → tier
    tier = "pro"
    for t, pid in PLAN_MAP.items():
        if pid == plan_id:
            tier = t
            break

    log.info(f"[RZP webhook] event={event} user={user_id} tier={tier}")

    if event in ("subscription.activated", "subscription.charged"):
        _upsert_subscription(user_id, tier, "active", rzp_sub_id, email)
    elif event in ("subscription.cancelled", "subscription.completed"):
        _upsert_subscription(user_id, "free", "cancelled", rzp_sub_id, email)
    elif event == "subscription.updated":
        _upsert_subscription(user_id, tier, "active", rzp_sub_id, email)
    elif event == "payment.failed":
        _upsert_subscription(user_id, tier, "payment_failed", rzp_sub_id, email)

    return {"status": "ok", "event": event}


@router.post("/billing/create-subscription", tags=["billing"])
def create_subscription(
    body: dict = {},
    x_user_id: Optional[str] = Header(None)
):
    """
    Creates a Razorpay subscription and returns subscription_id + key_id
    for the frontend Razorpay JS SDK to open the checkout modal.
    """
    user_id = x_user_id or "anonymous"
    tier    = body.get("tier", "pro")
    email   = body.get("email", "")
    plan_id = PLAN_MAP.get(tier, PLAN_MAP["pro"])

    if not plan_id:
        raise HTTPException(status_code=500, detail="Plan ID not configured")

    try:
        client = _rzp()
        sub = client.subscription.create({
            "plan_id":         plan_id,
            "total_count":     120,   # 10 years max, cancel anytime
            "quantity":        1,
            "customer_notify": 1,
            "notes": {
                "user_id": user_id,
                "email":   email,
                "tier":    tier,
            }
        })
        return {
            "subscription_id": sub["id"],
            "key_id":          RZP_KEY_ID,
            "tier":            tier,
            "plan_id":         plan_id,
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
        "user_id": user_id,
        "tier":    tier,
        "plan":    plan,
        "usage": {
            "signals": {"used": usage.get("signals", 0), "limit": plan["signals_per_day"]},
            "perseus": {"used": usage.get("perseus", 0), "limit": plan["perseus_per_day"]},
        },
    }


@router.get("/billing/plans", tags=["billing"])
def list_plans():
    from app.domain.billing.plans import PLANS
    return {"plans": PLANS}
