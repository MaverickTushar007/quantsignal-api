"""
api/chat.py
Streaming chat endpoint for the Agent Workspace.
Yields status updates followed by the LLM markdown response.
"""
import json
from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import List
from app.domain.reasoning.service import stream_chat
from app.domain.billing.middleware import perseus_gate

# ── Input moderation ────────────────────────────────────────────────────────
_BLOCKED = [
    "ignore previous", "ignore all instructions", "disregard your",
    "you are now", "pretend you are", "act as if", "jailbreak",
    "dan mode", "developer mode", "system prompt", "reveal your prompt",
    "forget your instructions", "bypass", "override instructions",
]

def _moderate(message: str):
    lower = message.lower()
    for pattern in _BLOCKED:
        if pattern in lower:
            return "This query was flagged by our content filter. Please ask a market-related question."
    return None

router = APIRouter()

class ChatMessage(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    symbol: str = "GENERIC"
    history: List[ChatMessage]
    message: str
    user_id: str = "default"
    mode: str = "auto"

@router.post("/chat/{symbol}", tags=["chat"])
async def chat_endpoint(symbol: str, request: ChatRequest):
    blocked = _moderate(request.message)
    if blocked:
        async def _err():
            yield f"data: {json.dumps({'type': 'error', 'message': blocked})}\n\n"
        return StreamingResponse(_err(), media_type="text/event-stream")
    return StreamingResponse(
        stream_chat(symbol.upper(), request.message, [h.dict() for h in request.history], request.user_id, mode=request.mode),
        media_type="text/event-stream"
    )

@router.post("/chat", tags=["chat"])
async def generic_chat_endpoint(request: ChatRequest):
    blocked = _moderate(request.message)
    if blocked:
        async def _err():
            yield f"data: {json.dumps({'type': 'error', 'message': blocked})}\n\n"
        return StreamingResponse(_err(), media_type="text/event-stream")
    return StreamingResponse(
        stream_chat(request.symbol.upper(), request.message, [h.dict() for h in request.history], request.user_id),
        media_type="text/event-stream"
    )
