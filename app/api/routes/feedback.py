from fastapi import APIRouter
from pydantic import BaseModel
import os
from supabase import create_client

router = APIRouter()

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_KEY", "")

def _sb():
    return create_client(SUPABASE_URL, SUPABASE_KEY)

class FeedbackPayload(BaseModel):
    name: str
    email: str
    rating: int
    message: str

@router.post("/feedback")
def submit_feedback(payload: FeedbackPayload):
    try:
        _sb().table("feedback").insert({
            "name": payload.name,
            "email": payload.email,
            "rating": payload.rating,
            "message": payload.message,
        }).execute()
        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "detail": str(e)}

@router.get("/feedback")
def get_feedback():
    try:
        res = _sb().table("feedback").select("*").order("created_at", desc=True).execute()
        return {"feedback": res.data}
    except Exception as e:
        return {"feedback": [], "error": str(e)}
