from fastapi import APIRouter, HTTPException

import db
from services import mcp_registry
from services.mcp_registry import McpDiscoveryError, McpServerInUseError

router = APIRouter(prefix="/api/mcp-servers", tags=["mcp-servers"])


def _serialise(row, *, include_tools: bool = True) -> dict:
    payload = {
        "id": str(row["id"]),
        "name": row["name"],
        "description": row["description"],
        "transport": row["transport"],
        "url": row["url"],
        "wired": row["wired"],
        "status": row["status"],
        "lastError": row["last_error"],
        "toolCount": row["tool_count"],
        "lastConnectedAt": (
            row["last_connected_at"].isoformat() if row["last_connected_at"] else None
        ),
    }
    if include_tools:
        # Live from the cache rather than the database — the row only stores a
        # count, because a stored tool list would go stale silently whenever an
        # MCP server is redeployed.
        payload["tools"] = [t.name for t in mcp_registry.cached_tools(row["name"])]
    return payload


@router.get("")
async def list_mcp_servers():
    """The catalog: every MCP server this app supports, wired or not.

    This is the checkbox list. `wired` is the tick, `status` and `lastError`
    say whether the last connection attempt worked.
    """
    rows = await db.fetch("SELECT * FROM mcp_servers ORDER BY name")
    return [_serialise(r) for r in rows]


@router.post("/{name}/wire")
async def wire_mcp_server(name: str):
    """Tick the box: connect, run tools/list, cache the tools.

    Fails loudly with the reason if the server is unreachable, and leaves the
    row unwired — better to find out here than to have an agent discover it
    mid-conversation.
    """
    try:
        await mcp_registry.wire(name)
    except McpDiscoveryError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    row = await db.fetchrow("SELECT * FROM mcp_servers WHERE name = $1", name)
    return _serialise(row)


@router.post("/{name}/refresh")
async def refresh_mcp_server(name: str):
    """Re-run tools/list, e.g. after the MCP server was redeployed.

    Every agent bound to this server picks up the new tool list at once.
    """
    try:
        await mcp_registry.refresh(name)
    except McpDiscoveryError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    row = await db.fetchrow("SELECT * FROM mcp_servers WHERE name = $1", name)
    return _serialise(row)


@router.post("/{name}/unwire")
async def unwire_mcp_server(name: str):
    """Untick the box.

    Refused with 409 while any agent is still bound to this server — detach it
    from those agents first. Use GET /dependents to list them.
    """
    row = await db.fetchrow("SELECT * FROM mcp_servers WHERE name = $1", name)
    if not row:
        raise HTTPException(status_code=404, detail=f"No MCP server '{name}'")

    try:
        await mcp_registry.unwire(name)
    except McpServerInUseError as exc:
        raise HTTPException(
            status_code=409,
            detail={"message": str(exc), "agents": exc.agents},
        )

    row = await db.fetchrow("SELECT * FROM mcp_servers WHERE name = $1", name)
    return _serialise(row)


@router.get("/{name}/dependents")
async def list_dependents(name: str):
    """Agents that would lose tools if this server were unwired.

    There is no authentication in this app, so the server catalog is shared:
    anyone can untick a box that other people's agents depend on. The UI uses
    this to warn before that happens.
    """
    row = await db.fetchrow("SELECT id FROM mcp_servers WHERE name = $1", name)
    if not row:
        raise HTTPException(status_code=404, detail=f"No MCP server '{name}'")

    agents = await db.fetch(
        """
        SELECT a.id, a.name
          FROM agents a
          JOIN agent_mcp_servers ams ON ams.agent_id = a.id
         WHERE ams.mcp_server_id = $1
         ORDER BY a.name
        """,
        row["id"],
    )
    return {
        "count": len(agents),
        "agents": [{"id": str(a["id"]), "name": a["name"]} for a in agents],
    }
