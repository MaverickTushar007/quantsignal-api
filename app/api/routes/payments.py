"""
api/payments.py
Lemon Squeezy checkout + webhook handler.
Creates checkout URLs and updates Supabase profiles on payment.
"""
from fastapi import APIRouter, HTTPException, Request, Header
from pydantic import BaseModel
import os, hmac, hashlib, requests
from supabase import create_client

router = APIRouter()

LS_API_KEY     = os.getenv("LEMONSQUEEZY_API_KEY", "")
LS_VARIANT_ID  = os.getenv("LEMONSQUEEZY_VARIANT_ID", "")
LS_STORE_ID    = os.getenv("LEMONSQUEEZY_STORE_ID", "")
LS_WEBHOOK_SECRET = os.getenv("LEMONSQUEEZY_WEBHOOK_SECRET", "")
SUPABASE_URL   = os.getenv("NEXT_PUBLIC_SUPABASE_URL", "https://xvwkloqmzgwqsouxhgiy.supabase.co")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")

class CheckoutRequest(BaseModel):
    email: str
    user_id: str

@router.post("/payments/checkout", tags=["payments"])
def create_checkout(req: CheckoutRequest):
    if not LS_API_KEY or not LS_VARIANT_ID:
        raise HTTPException(status_code=500, detail="Payment not configured")

    try:
        res = requests.post(
            "https://api.lemonsqueezy.com/v1/checkouts",
            headers={
                "Authorization": f"Bearer {LS_API_KEY}",
                "Accept": "application/vnd.api+json",
                "Content-Type": "application/vnd.api+json",
            },
            json={
                "data": {
                    "type": "checkouts",
                    "attributes": {
                        "checkout_data": {
                            "email": req.email,
                            "custom": {"user_id": req.user_id}
                        },
                        "product_options": {
                            "redirect_url": "https://quantsignal.app/dashboard?upgraded=true",
                        }
                    },
                    "relationships": {
                        "store": {
                            "data": {"type": "stores", "id": str(LS_STORE_ID)}
                        },
                        "variant": {
                            "data": {"type": "variants", "id": str(LS_VARIANT_ID)}
                        }
                    }
                }
            },
            timeout=15
        )
        data = res.json()
        if res.status_code not in [200, 201]:
            raise HTTPException(status_code=500, detail=str(data))
        checkout_url = data["data"]["attributes"]["url"]
        return {"checkout_url": checkout_url}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/payments/webhook", tags=["payments"])
async def webhook(request: Request, x_signature: str = Header(None, alias="X-Signature")):
    body = await request.body()

    # Verify webhook signature
    if LS_WEBHOOK_SECRET and x_signature:
        expected = hmac.new(
            LS_WEBHOOK_SECRET.encode(),
            body,
            hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected, x_signature or ""):
            raise HTTPException(status_code=401, detail="Invalid signature")

    payload = await request.json()
    event = payload.get("meta", {}).get("event_name", "")
    custom = payload.get("meta", {}).get("custom_data", {})
    user_id = custom.get("user_id")

    if not user_id:
        return {"status": "ignored", "reason": "no user_id"}

    # Update Supabase profile on successful payment
    if event in ["subscription_created", "subscription_updated", "order_created"]:
        try:
            sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
            attrs = payload.get("data", {}).get("attributes", {})
            subscription_id = payload.get("data", {}).get("id", "")
            customer_id = str(attrs.get("customer_id", ""))

            sb.table("profiles").upsert({
                "id": user_id,
                "is_pro": True,
                "tier": "pro",
                "ls_customer_id": customer_id,
                "ls_order_id": subscription_id,
                "upgraded_at": attrs.get("created_at"),
            }).execute()
        except Exception as e:
            print(f"Supabase update failed: {e}")

    # Downgrade on cancellation
    if event in ["subscription_expired", "subscription_cancelled"]:
        try:
            sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
            sb.table("profiles").update({"is_pro": False, "tier": "free"}).eq("id", user_id).execute()
        except Exception as e:
            print(f"Supabase downgrade failed: {e}")

    return {"status": "ok", "event": event}
