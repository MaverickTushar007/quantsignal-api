"""
api/agents.py
Virtual Agent Paper Trading — CRUD + executor logic.
"""
import os, json
from datetime import datetime, timezone, timedelta
from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import urllib.request

router = APIRouter()

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SERVICE_KEY  = os.getenv("SUPABASE_SERVICE_KEY", "")

HEADERS = {
    "apikey": SERVICE_KEY,
    "Authorization": f"Bearer {SERVICE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
}

STRATEGY_FILTERS = {
    "india":  lambda s: s.endswith(".NS") or s.endswith(".BO"),
    "crypto": lambda s: s.endswith("-USD") and s not in ["BTC-USD"],
    "crypto_major": lambda s: s in ["BTC-USD", "ETH-USD", "BNB-USD"],
    "us":     lambda s: not s.endswith(".NS") and not s.endswith("-USD"),
    "all":    lambda s: True,
}

# ── Supabase helpers ──────────────────────────────────────────────────────────

def _sb_get(table: str, params: str = "") -> list:
    url = f"{SUPABASE_URL}/rest/v1/{table}?{params}"
    req = urllib.request.Request(url, headers=HEADERS)
    resp = urllib.request.urlopen(req)
    return json.loads(resp.read())

def _sb_post(table: str, payload: dict) -> dict:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{SUPABASE_URL}/rest/v1/{table}",
        data=data, headers=HEADERS, method="POST"
    )
    resp = urllib.request.urlopen(req)
    result = json.loads(resp.read())
    return result[0] if isinstance(result, list) else result

def _sb_patch(table: str, row_id: str, payload: dict):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{SUPABASE_URL}/rest/v1/{table}?id=eq.{row_id}",
        data=data, headers={**HEADERS, "Prefer": "return=minimal"},
        method="PATCH"
    )
    urllib.request.urlopen(req)

# ── Schemas ───────────────────────────────────────────────────────────────────

class AgentCreate(BaseModel):
    user_id: str
    name: str
    strategy: str = "all"          # india | crypto | us | all
    min_probability: float = 0.65  # 0.60 – 0.90
    budget_inr: float = 100000     # virtual capital

class AgentUpdate(BaseModel):
    status: Optional[str] = None
    min_probability: Optional[float] = None

# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/agents", tags=["agents"])
def create_agent(body: AgentCreate):
    """Create a new virtual trading agent."""
    if body.min_probability < 0.5 or body.min_probability > 0.95:
        raise HTTPException(400, "min_probability must be between 0.5 and 0.95")
    if body.budget_inr < 10000:
        raise HTTPException(400, "Minimum budget is ₹10,000")
    if body.strategy not in STRATEGY_FILTERS:
        raise HTTPException(400, f"strategy must be one of {list(STRATEGY_FILTERS)}")

    agent = _sb_post("agents", {
        "user_id": body.user_id,
        "name": body.name,
        "strategy": body.strategy,
        "min_probability": body.min_probability,
        "budget_inr": body.budget_inr,
        "status": "active",
        "consecutive_losses": 0,
        "total_pnl_inr": 0,
        "total_trades": 0,
    })
    return {"agent": agent, "message": f"Agent '{body.name}' created successfully"}


@router.get("/agents/{user_id}", tags=["agents"])
def get_agents(user_id: str):
    """Get all agents for a user."""
    agents = _sb_get("agents", f"user_id=eq.{user_id}&order=created_at.desc")
    for a in agents:
        # Attach recent trades summary
        try:
            trades = _sb_get("agent_trades",
                f"agent_id=eq.{a['id']}&order=opened_at.desc&limit=5")
            a["recent_trades"] = trades
            open_trades = _sb_get("agent_trades",
                f"agent_id=eq.{a['id']}&outcome=eq.open")
            a["open_positions"] = len(open_trades)
        except Exception:
            a["recent_trades"] = []
            a["open_positions"] = 0
    return {"agents": agents}


@router.get("/agents/{user_id}/{agent_id}/trades", tags=["agents"])
def get_agent_trades(user_id: str, agent_id: str, limit: int = 50):
    """Get trade history for a specific agent."""
    trades = _sb_get("agent_trades",
        f"agent_id=eq.{agent_id}&order=opened_at.desc&limit={limit}")
    return {"trades": trades, "count": len(trades)}


@router.patch("/agents/{agent_id}", tags=["agents"])
def update_agent(agent_id: str, body: AgentUpdate):
    """Pause, resume, or update agent settings."""
    payload = {k: v for k, v in body.dict().items() if v is not None}
    if not payload:
        raise HTTPException(400, "Nothing to update")
    _sb_patch("agents", agent_id, payload)
    return {"message": "Agent updated"}


@router.delete("/agents/{agent_id}", tags=["agents"])
def delete_agent(agent_id: str):
    """Delete an agent and all its trades."""
    req = urllib.request.Request(
        f"{SUPABASE_URL}/rest/v1/agents?id=eq.{agent_id}",
        headers={**HEADERS, "Prefer": "return=minimal"},
        method="DELETE"
    )
    urllib.request.urlopen(req)
    return {"message": "Agent deleted"}
