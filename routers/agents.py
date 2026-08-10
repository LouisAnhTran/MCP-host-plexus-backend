import asyncio
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

import db
from models import CreateAgentRequest, UpdateAgentRequest
from services.kb.zendesk import parse_zendesk_url

router = APIRouter(prefix="/api/agents", tags=["agents"])


def _serialize(row, mcp_server_names: list[str] | None = None) -> dict:
    return {
        "id": str(row["id"]),
        "name": row["name"],
        "kb_url": row["kb_url"],
        "mcp_server_names": mcp_server_names or [],
        "status": row["status"],
        "error_message": row["error_message"],
        "last_indexed_at": row["last_indexed_at"].isoformat() if row["last_indexed_at"] else None,
        "created_at": row["created_at"].isoformat(),
        "updated_at": row["updated_at"].isoformat(),
    }


async def _server_names_for(agent_id) -> list[str]:
    rows = await db.fetch(
        """
        SELECT s.name
          FROM mcp_servers s
          JOIN agent_mcp_servers ams ON ams.mcp_server_id = s.id
         WHERE ams.agent_id = $1
         ORDER BY s.name
        """,
        agent_id,
    )
    return [r["name"] for r in rows]


async def _validate_server_names(names: list[str]):
    """Reject unknown or unwired servers.

    Called *before* inserting an agent, not during binding: doing it after the
    INSERT leaves an orphaned agent behind when validation fails, since the two
    statements are not in one transaction.

    Unwired servers are refused because they have no discovered tools — binding
    one would look like granting a capability while granting nothing.
    """
    if not names:
        return

    rows = await db.fetch(
        "SELECT name, wired FROM mcp_servers WHERE name = ANY($1::text[])",
        names,
    )
    found = {r["name"]: r["wired"] for r in rows}

    unknown = [n for n in names if n not in found]
    if unknown:
        raise HTTPException(
            400, detail=f"Unknown MCP server(s): {', '.join(sorted(unknown))}"
        )

    not_wired = [n for n in names if not found[n]]
    if not_wired:
        raise HTTPException(
            400,
            detail=(
                f"MCP server(s) not wired: {', '.join(sorted(not_wired))}. "
                f"Wire them first so their tools can be discovered."
            ),
        )


async def _bind_servers(agent_id, names: list[str]):
    """Replace an agent's MCP server bindings. Validate the names first."""
    await db.execute("DELETE FROM agent_mcp_servers WHERE agent_id = $1", agent_id)
    if names:
        await db.execute(
            """
            INSERT INTO agent_mcp_servers (agent_id, mcp_server_id)
            SELECT $1, id FROM mcp_servers WHERE name = ANY($2::text[])
            """,
            agent_id,
            names,
        )


def _validate_url(kb_url: str | None):
    """Every agent needs a Zendesk knowledge base."""
    if not kb_url or not kb_url.strip():
        raise HTTPException(400, detail="kb_url is required")
    try:
        parse_zendesk_url(kb_url)
    except ValueError as e:
        raise HTTPException(400, detail=str(e))


@router.post("", status_code=201)
async def create_agent(body: CreateAgentRequest):
    _validate_url(body.kb_url)
    # Before the INSERT: a failure here must not leave an orphaned agent.
    await _validate_server_names(body.mcp_server_names)

    # Only go into 'indexing' when there is actually a knowledge base to crawl.
    # An MCP-only agent is usable immediately.
    has_kb = bool(body.kb_url and body.kb_url.strip())
    row = await db.fetchrow(
        """
        INSERT INTO agents (name, kb_url, status)
        VALUES ($1, $2, $3)
        RETURNING *
        """,
        body.name,
        body.kb_url or None,
        "indexing" if has_kb else "ready",
    )

    agent_id = row["id"]
    await _bind_servers(agent_id, body.mcp_server_names)

    if has_kb:
        asyncio.create_task(_run_indexing(str(agent_id)))

    names = await _server_names_for(agent_id)
    return JSONResponse(_serialize(row, names), status_code=201)


@router.get("")
async def list_agents():
    # One query rather than a lookup per agent — the list view would otherwise
    # issue N+1 round trips as the number of agents grows.
    rows = await db.fetch(
        """
        SELECT a.*,
               COALESCE(
                   ARRAY_AGG(s.name ORDER BY s.name)
                   FILTER (WHERE s.name IS NOT NULL),
                   '{}'
               ) AS mcp_server_names
          FROM agents a
          LEFT JOIN agent_mcp_servers ams ON ams.agent_id = a.id
          LEFT JOIN mcp_servers s         ON s.id = ams.mcp_server_id
         GROUP BY a.id
         ORDER BY a.created_at DESC
        """
    )
    return [_serialize(r, list(r["mcp_server_names"])) for r in rows]


@router.get("/{agent_id}")
async def get_agent(agent_id: str):
    row = await db.fetchrow("SELECT * FROM agents WHERE id = $1", agent_id)
    if not row:
        raise HTTPException(404, detail="Agent not found")
    return _serialize(row, await _server_names_for(row["id"]))


@router.put("/{agent_id}")
async def update_agent(agent_id: str, body: UpdateAgentRequest):
    row = await db.fetchrow("SELECT id FROM agents WHERE id = $1", agent_id)
    if not row:
        raise HTTPException(404, detail="Agent not found")

    _validate_url(body.kb_url)
    # Before the UPDATE, so a bad server name doesn't half-apply the change.
    await _validate_server_names(body.mcp_server_names)

    # Reindexing only means anything if there is a knowledge base to crawl.
    has_kb = bool(body.kb_url and body.kb_url.strip())
    should_reindex = body.reindex and has_kb

    if should_reindex:
        updated = await db.fetchrow(
            """
            UPDATE agents
            SET name = $1, kb_url = $2,
                status = 'indexing', error_message = NULL, updated_at = NOW()
            WHERE id = $3
            RETURNING *
            """,
            body.name, body.kb_url, agent_id,
        )
    else:
        updated = await db.fetchrow(
            """
            UPDATE agents
            SET name = $1, kb_url = $2, updated_at = NOW()
            WHERE id = $3
            RETURNING *
            """,
            body.name, body.kb_url or None, agent_id,
        )

    await _bind_servers(updated["id"], body.mcp_server_names)

    if should_reindex:
        asyncio.create_task(_run_indexing(agent_id))

    return _serialize(updated, await _server_names_for(updated["id"]))


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
