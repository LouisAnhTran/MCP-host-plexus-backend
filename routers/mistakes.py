import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

import db
from models import CreateMistakeRequest
from services.mistakes import apply_fix

logger = logging.getLogger(__name__)
router = APIRouter(tags=["mistakes"])


def _serialize(row) -> dict:
    return {
        "id": str(row["id"]),
        "agent_id": str(row["agent_id"]),
        "user_message": row["user_message"],
        "bot_response": row["bot_response"],
        "user_description": row["user_description"],
        "status": row["status"],
        "fix_comment": row["fix_comment"],
        "verified_response": row["verified_response"],
        "created_at": row["created_at"].isoformat(),
        "resolved_at": row["resolved_at"].isoformat() if row["resolved_at"] else None,
    }


@router.post("/api/agents/{agent_id}/mistakes", status_code=201)
async def create_mistake(agent_id: str, body: CreateMistakeRequest):
    agent = await db.fetchrow("SELECT id FROM agents WHERE id = $1", agent_id)
    if not agent:
        raise HTTPException(404, detail="Agent not found")

    row = await db.fetchrow(
        """
        INSERT INTO mistake_reports (agent_id, user_message, bot_response, user_description)
        VALUES ($1, $2, $3, $4)
        RETURNING *
        """,
        agent_id, body.user_message, body.bot_response, body.user_description,
    )
    return JSONResponse(_serialize(row), status_code=201)


@router.get("/api/agents/{agent_id}/mistakes")
async def list_mistakes(agent_id: str):
    agent = await db.fetchrow("SELECT id FROM agents WHERE id = $1", agent_id)
    if not agent:
        raise HTTPException(404, detail="Agent not found")

    rows = await db.fetch(
        "SELECT * FROM mistake_reports WHERE agent_id = $1 ORDER BY created_at DESC",
        agent_id,
    )
    return [_serialize(r) for r in rows]


@router.put("/api/mistakes/{mistake_id}/fix")
async def run_fix(mistake_id: str):
    try:
        updated = await apply_fix(mistake_id)
    except ValueError as e:
        raise HTTPException(400, detail=str(e))
    except Exception:
        logger.exception("Fix failed for mistake %s", mistake_id)
        raise HTTPException(500, detail="Fix failed. See server logs.")
    return _serialize(updated)
