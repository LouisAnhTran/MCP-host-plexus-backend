import asyncio
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

import db
from models import CreateAgentRequest, UpdateAgentRequest
from services.kb.zendesk import parse_zendesk_url

router = APIRouter(prefix="/api/agents", tags=["agents"])


def _serialize(row) -> dict:
    return {
        "id": str(row["id"]),
        "name": row["name"],
        "kb_url": row["kb_url"],
        "instructions": row["instructions"] or [],
        "status": row["status"],
        "error_message": row["error_message"],
        "last_indexed_at": row["last_indexed_at"].isoformat() if row["last_indexed_at"] else None,
        "created_at": row["created_at"].isoformat(),
        "updated_at": row["updated_at"].isoformat(),
    }


def _validate_url(kb_url: str):
    if not kb_url or not kb_url.strip():
        raise HTTPException(400, detail="kb_url is required")
    try:
        parse_zendesk_url(kb_url)
    except ValueError as e:
        raise HTTPException(400, detail=str(e))


@router.post("", status_code=201)
async def create_agent(body: CreateAgentRequest):
    _validate_url(body.kb_url)

    instructions = [i.model_dump() for i in body.instructions]
    row = await db.fetchrow(
        """
        INSERT INTO agents (name, kb_url, instructions, status)
        VALUES ($1, $2, $3, 'indexing')
        RETURNING *
        """,
        body.name, body.kb_url, instructions,
    )

    agent_id = str(row["id"])
    asyncio.create_task(_run_indexing(agent_id))
    return JSONResponse(_serialize(row), status_code=201)


@router.get("")
async def list_agents():
    rows = await db.fetch("SELECT * FROM agents ORDER BY created_at DESC")
    return [_serialize(r) for r in rows]


@router.get("/{agent_id}")
async def get_agent(agent_id: str):
    row = await db.fetchrow("SELECT * FROM agents WHERE id = $1", agent_id)
    if not row:
        raise HTTPException(404, detail="Agent not found")
    return _serialize(row)


@router.put("/{agent_id}")
async def update_agent(agent_id: str, body: UpdateAgentRequest):
    row = await db.fetchrow("SELECT id FROM agents WHERE id = $1", agent_id)
    if not row:
        raise HTTPException(404, detail="Agent not found")

    _validate_url(body.kb_url)

    instructions = [i.model_dump() for i in body.instructions]
    new_status = "indexing" if body.reindex else None

    if new_status:
        updated = await db.fetchrow(
            """
            UPDATE agents
            SET name = $1, kb_url = $2, instructions = $3,
                status = 'indexing', error_message = NULL, updated_at = NOW()
            WHERE id = $4
            RETURNING *
            """,
            body.name, body.kb_url, instructions, agent_id,
        )
        asyncio.create_task(_run_indexing(agent_id))
    else:
        updated = await db.fetchrow(
            """
            UPDATE agents
            SET name = $1, kb_url = $2, instructions = $3, updated_at = NOW()
            WHERE id = $4
            RETURNING *
            """,
            body.name, body.kb_url, instructions, agent_id,
        )

    return _serialize(updated)


@router.delete("/{agent_id}", status_code=204)
async def delete_agent(agent_id: str):
    result = await db.execute("DELETE FROM agents WHERE id = $1", agent_id)
    if result == "DELETE 0":
        raise HTTPException(404, detail="Agent not found")


async def _run_indexing(agent_id: str):
    """Stub — full implementation in Step 4."""
    try:
        from services.kb.indexer import run_indexing_pipeline
        await run_indexing_pipeline(agent_id)
    except Exception as e:
        await db.execute(
            "UPDATE agents SET status = 'failed', error_message = $1 WHERE id = $2",
            str(e)[:500], agent_id,
        )
