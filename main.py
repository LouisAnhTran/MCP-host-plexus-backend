import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)

from db import init_db, close_db
from routers import health, agents, chat, mistakes, tools, proxy


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    # Recover agents stuck in 'indexing' from a previous crashed run
    from db import execute
    await execute(
        "UPDATE agents SET status = 'failed', error_message = 'Interrupted by restart' "
        "WHERE status = 'indexing'"
    )
    yield
    await close_db()


app = FastAPI(title="CS Meta-Agent API", lifespan=lifespan)

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
app.include_router(tools.router)
app.include_router(proxy.router)
