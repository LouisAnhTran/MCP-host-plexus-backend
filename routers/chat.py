from fastapi import APIRouter, HTTPException

import db
from models import ChatRequest, ChatResponse
from services.runtime import run_chat

router = APIRouter(tags=["chat"])


@router.post("/api/agents/{agent_id}/chat", response_model=ChatResponse)
async def chat(agent_id: str, body: ChatRequest):
    agent = await db.fetchrow("SELECT id, status FROM agents WHERE id = $1", agent_id)
    if not agent:
        raise HTTPException(404, detail="Agent not found")

    if agent["status"] == "indexing":
        raise HTTPException(503, detail="Agent is currently indexing. Please wait.")
    if agent["status"] == "failed":
        raise HTTPException(503, detail="Agent indexing failed. Please re-index before chatting.")

    messages = [m.model_dump() for m in body.messages]
    result = await run_chat(agent_id, messages)
    return result
