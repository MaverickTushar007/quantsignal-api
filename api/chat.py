"""
api/chat.py
Streaming chat endpoint for the Agent Workspace.
Yields status updates followed by the LLM markdown response.
"""
from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import List

from core.reasoning import stream_chat
from api.auth import get_current_user

router = APIRouter()

class ChatMessage(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    symbol: str
    history: List[ChatMessage]
    message: str

@router.post("/chat/{symbol}", tags=["chat"])
async def chat_endpoint(
    symbol: str,
    request: ChatRequest,
    user: dict = Depends(get_current_user)
):
    if symbol.upper() != request.symbol.upper():
        raise HTTPException(status_code=400, detail="Symbol mismatch")

    # FastAPI's StreamingResponse takes an async generator
    # stream_chat handles the workflow and yields SSE-formatted strings.
    return StreamingResponse(
        stream_chat(request.symbol.upper(), request.message, [h.dict() for h in request.history]),
        media_type="text/event-stream"
    )
