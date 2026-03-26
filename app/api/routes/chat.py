"""
api/chat.py
Streaming chat endpoint for the Agent Workspace.
Yields status updates followed by the LLM markdown response.
"""
from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import List

from app.domain.reasoning.service import stream_chat


router = APIRouter()

class ChatMessage(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    symbol: str = "GENERIC"
    history: List[ChatMessage]
    message: str

@router.post("/chat/{symbol}", tags=["chat"])
async def chat_endpoint(
    symbol: str,
    request: ChatRequest,

):
    # stream_chat handles the workflow and yields SSE-formatted strings.
    return StreamingResponse(
        stream_chat(symbol.upper(), request.message, [h.dict() for h in request.history]),
        media_type="text/event-stream"
    )

@router.post("/chat", tags=["chat"])
async def generic_chat_endpoint(
    request: ChatRequest,

):
    return StreamingResponse(
        stream_chat(request.symbol.upper(), request.message, [h.dict() for h in request.history]),
        media_type="text/event-stream"
    )
