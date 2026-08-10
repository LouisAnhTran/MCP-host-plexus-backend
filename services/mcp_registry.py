"""Connects to MCP servers, discovers their tools, and caches them.

Three states a server can be in:

    SUPPORTED   declared in mcp_catalog.py, seeded as an mcp_servers row
    WIRED       connected once, tools discovered and cached  (wired = true)
    BOUND       some agent selected it                       (agent_mcp_servers)

The cache holds LangChain tools keyed by server name. It lives in memory only,
which is why `wired` is persisted: on startup we re-read the wired rows and
rediscover. A tools list in the database would go stale the moment someone
redeploys an MCP server with different tools, and nothing would notice.

Nothing here holds a live connection. langchain-mcp-adapters opens a fresh
session per tool *call*, so a cached tool is just a callable that knows a URL —
there is no socket to keep alive, no reconnect logic, and idling costs nothing.
Only the tool *list* can go stale, which is what refresh() is for.
"""

import asyncio
import logging

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient

import db

logger = logging.getLogger(__name__)

# How long to wait for a server to answer initialize + tools/list. Bounded so
# one unreachable server cannot stall application startup.
DISCOVERY_TIMEOUT_SECONDS = 15

# server name -> the LangChain tools it exposes
_tool_cache: dict[str, list[BaseTool]] = {}


class McpDiscoveryError(Exception):
    """Connecting to or listing tools from an MCP server failed."""


class McpServerInUseError(Exception):
    """The server cannot be unwired because agents are still bound to it."""

    def __init__(self, message: str, agents: list[str]):
        super().__init__(message)
        self.agents = agents


def _connection(url: str, transport: str) -> dict:
    # langchain-mcp-adapters spells streamable HTTP "streamable_http"; our
    # catalog and database use the shorter "http" that MCP itself uses.
    return {
        "transport": "streamable_http" if transport in ("http", "streamable_http") else transport,
        "url": url,
    }


async def _discover(name: str, url: str, transport: str) -> list[BaseTool]:
    """Run initialize + tools/list against one server.

    Raises McpDiscoveryError with a message intended to be shown to whoever
    ticked the checkbox — they need to know *why* it failed, not just that it
    did.
    """
    client = MultiServerMCPClient(
        {name: _connection(url, transport)},
        # Namespaces tools as "<server>_<tool>", so two servers exposing a
        # tool of the same name cannot collide in one agent's tool list.
        tool_name_prefix=True,
    )
    try:
        return await asyncio.wait_for(
            client.get_tools(server_name=name),
            timeout=DISCOVERY_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        raise McpDiscoveryError(
            f"{url} did not respond within {DISCOVERY_TIMEOUT_SECONDS}s."
        )
    except Exception as exc:  # noqa: BLE001 — surface whatever the transport raised
        raise McpDiscoveryError(f"Could not reach {url} — {_root_cause(exc)}")


def _root_cause(exc: BaseException) -> str:
    """Describe the innermost real error.

    The MCP client runs its transport inside an anyio TaskGroup, so a refused
    connection surfaces as "unhandled errors in a TaskGroup (1 sub-exception)"
    — which tells whoever ticked the checkbox nothing at all. Unwrap the group
    and any __cause__ chain to reach the error that actually happened.
    """
    seen: set[int] = set()
    while id(exc) not in seen:
        seen.add(id(exc))
        sub = getattr(exc, "exceptions", None)  # ExceptionGroup
        if sub:
            exc = sub[0]
            continue
        if exc.__cause__ is not None:
            exc = exc.__cause__
            continue
        break

    detail = str(exc).strip()
    return f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__


async def _mark_connected(name: str, tool_count: int):
    await db.execute(
        """
        UPDATE mcp_servers
           SET wired = TRUE, status = 'connected', last_error = NULL,
               tool_count = $1, last_connected_at = NOW(), updated_at = NOW()
         WHERE name = $2
        """,
        tool_count,
        name,
    )


async def _mark_failed(name: str, error: str, *, wired: bool):
    await db.execute(
        """
        UPDATE mcp_servers
           SET wired = $1, status = 'failed', last_error = $2,
               tool_count = NULL, updated_at = NOW()
         WHERE name = $3
        """,
        wired,
        error,
        name,
    )


async def wire(name: str) -> dict:
    """Tick the checkbox: connect, discover, cache, mark wired.

    On failure the row stays unwired and the reason is stored, so the UI can
    show why rather than leaving a box that silently refuses to tick.
    """
    server = await db.fetchrow("SELECT * FROM mcp_servers WHERE name = $1", name)
    if not server:
        raise McpDiscoveryError(f"No supported MCP server named '{name}'")

    try:
        tools = await _discover(name, server["url"], server["transport"])
    except McpDiscoveryError as exc:
        await _mark_failed(name, str(exc), wired=False)
        raise

    _tool_cache[name] = tools
    await _mark_connected(name, len(tools))
    logger.info("wired MCP server '%s' — %d tools", name, len(tools))
    return {"name": name, "toolCount": len(tools), "tools": [t.name for t in tools]}


async def refresh(name: str) -> dict:
    """Re-run tools/list for an already-wired server.

    The reason a server's tools can change without anything here changing: it
    was redeployed. Every agent bound to it picks up the new list at once.
    """
    server = await db.fetchrow("SELECT * FROM mcp_servers WHERE name = $1", name)
    if not server:
        raise McpDiscoveryError(f"No supported MCP server named '{name}'")
    if not server["wired"]:
        raise McpDiscoveryError(f"MCP server '{name}' is not wired")

    try:
        tools = await _discover(name, server["url"], server["transport"])
    except McpDiscoveryError as exc:
        # Stay wired: the intent to use this server has not changed, only its
        # current reachability. Keeping the old cache means an agent mid-
        # conversation still has its tools and gets a real error if it calls
        # one, instead of the model deciding it never had the capability.
        await _mark_failed(name, str(exc), wired=True)
        raise

    _tool_cache[name] = tools
    await _mark_connected(name, len(tools))
    logger.info("refreshed MCP server '%s' — %d tools", name, len(tools))
    return {"name": name, "toolCount": len(tools), "tools": [t.name for t in tools]}


async def unwire(name: str) -> dict:
    """Untick the checkbox: drop the cache and mark it unwired.

    Refused while any agent is still bound to this server. There is no
    authentication in this app, so the catalog is shared — without the guard,
    one person unticking a box would silently strip tools from other people's
    agents, and the only symptom would be an agent claiming it cannot do
    something it used to do.

    Detach the server from every agent first, then unwire.
    """
    bound = await db.fetch(
        """
        SELECT a.name
          FROM agents a
          JOIN agent_mcp_servers ams ON ams.agent_id = a.id
          JOIN mcp_servers s         ON s.id = ams.mcp_server_id
         WHERE s.name = $1
         ORDER BY a.name
        """,
        name,
    )
    if bound:
        names = [row["name"] for row in bound]
        raise McpServerInUseError(
            f"Cannot unwire '{name}': {len(names)} agent(s) still use it "
            f"({', '.join(names)}). Remove it from those agents first.",
            names,
        )

    _tool_cache.pop(name, None)
    await db.execute(
        """
        UPDATE mcp_servers
           SET wired = FALSE, status = 'unknown', last_error = NULL,
               tool_count = NULL, updated_at = NOW()
         WHERE name = $1
        """,
        name,
    )
    logger.info("unwired MCP server '%s'", name)
    return {"name": name, "wired": False}


async def reload_all_wired():
    """Rebuild the cache at startup for every server marked wired.

    Without this a restart leaves `wired = true` rows and an empty cache, so
    agents would silently have no MCP tools while the UI showed ticked boxes.

    A server that is down does not stop startup: it is marked failed, stays
    wired, and can be recovered with refresh().
    """
    rows = await db.fetch("SELECT * FROM mcp_servers WHERE wired = TRUE")
    if not rows:
        return

    for row in rows:
        name = row["name"]
        try:
            tools = await _discover(name, row["url"], row["transport"])
        except McpDiscoveryError as exc:
            await _mark_failed(name, str(exc), wired=True)
            logger.warning("MCP server '%s' unavailable at startup: %s", name, exc)
            continue
        _tool_cache[name] = tools
        await _mark_connected(name, len(tools))
        logger.info("reloaded MCP server '%s' — %d tools", name, len(tools))


def tools_for(server_names: list[str]) -> list[BaseTool]:
    """Cached tools for the given servers, in a stable order.

    Names with no cache entry are skipped silently — that means the server is
    wired but currently unreachable, which reload/refresh has already recorded
    on the row.
    """
    tools: list[BaseTool] = []
    for name in server_names:
        tools.extend(_tool_cache.get(name, []))
    return tools


def cached_tools(name: str) -> list[BaseTool]:
    return _tool_cache.get(name, [])


def cached_tool_names() -> set[str]:
    """Every tool name currently available across all wired servers."""
    return {tool.name for tools in _tool_cache.values() for tool in tools}
