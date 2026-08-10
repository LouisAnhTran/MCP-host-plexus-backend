import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)

from db import init_db, close_db  # noqa: E402
from routers import (  # noqa: E402
    agents,
    chat,
    health,
    mcp_servers,
    mistakes,
    proxy,
)
from services import mcp_registry  # noqa: E402


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()

    # Recover agents stuck in 'indexing' from a previous crashed run
    from db import execute
    await execute(
        "UPDATE agents SET status = 'failed', error_message = 'Interrupted by restart' "
        "WHERE status = 'indexing'"
    )

    # Rebuild the MCP tool cache. It lives in memory, so a restart would
    # otherwise leave wired servers with no tools while the UI showed them as
    # connected. A server that is down is logged and left wired rather than
    # blocking startup.
    await mcp_registry.reload_all_wired()

    yield
    await close_db()


app = FastAPI(title="Plexus MCP Host API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(agents.router)
app.include_router(chat.router)
app.include_router(mistakes.router)
app.include_router(proxy.router)
app.include_router(mcp_servers.router)
