import json
import logging

import asyncpg
from config import settings
from mcp_catalog import resolved_catalog

logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None


async def _init_connection(conn: asyncpg.Connection):
    await conn.set_type_codec(
        "jsonb",
        encoder=json.dumps,
        decoder=json.loads,
        schema="pg_catalog",
    )
    await conn.set_type_codec(
        "json",
        encoder=json.dumps,
        decoder=json.loads,
        schema="pg_catalog",
    )


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(settings.database_url, init=_init_connection)
    return _pool


async def init_db():
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS agents (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                name VARCHAR(255) NOT NULL,
                -- Nullable: an agent whose capabilities come entirely from MCP
                -- servers needs no Zendesk knowledge base.
                kb_url TEXT,
                -- Free text, the manager's extra prompt guidance. Tool binding
                -- used to live in here as {tool_name: ...} objects; it now
                -- lives in agent_mcp_servers.
                instructions TEXT,
                status VARCHAR(20) DEFAULT 'ready',
                error_message TEXT,
                last_indexed_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW()
            )
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS kb_articles (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                agent_id UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
                source_article_id BIGINT,
                article_title TEXT NOT NULL,
                article_url TEXT NOT NULL,
                section_name TEXT,
                body_text TEXT NOT NULL,
                embedding vector(1536) NOT NULL,
                created_at TIMESTAMP DEFAULT NOW(),
                UNIQUE (agent_id, source_article_id)
            )
        """)

        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_kb_articles_embedding
                ON kb_articles USING hnsw (embedding vector_cosine_ops)
        """)

        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_kb_articles_agent_section
                ON kb_articles (agent_id, section_name)
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS mistake_reports (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                agent_id UUID REFERENCES agents(id) ON DELETE CASCADE,
                user_message TEXT NOT NULL,
                bot_response TEXT NOT NULL,
                user_description TEXT,
                status VARCHAR(20) DEFAULT 'open',
                fix_comment TEXT,
                verified_response TEXT,
                created_at TIMESTAMP DEFAULT NOW(),
                resolved_at TIMESTAMP
            )
        """)

        # ── MCP servers ──────────────────────────────────────────────────────
        # One row per server this app supports, seeded from mcp_catalog.py.
        # `wired` is the checkbox: false means "known about but not connected".
        # The discovered tools are NOT stored here — they live in an in-memory
        # cache, because a redeployed MCP server can change its tool list at
        # any time and a database copy would go stale silently.
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS mcp_servers (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                name VARCHAR(64) NOT NULL UNIQUE,
                description TEXT,
                transport VARCHAR(20) NOT NULL DEFAULT 'http',
                url TEXT NOT NULL,
                wired BOOLEAN NOT NULL DEFAULT FALSE,
                status VARCHAR(20) NOT NULL DEFAULT 'unknown',
                last_error TEXT,
                tool_count INTEGER,
                last_connected_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)

        # Which agents may use which servers. A real join table rather than an
        # array column on `agents`, so a foreign key can enforce that a bound
        # server exists, and so "which agents break if I unwire this?" is a
        # cheap query rather than a scan over every agent's JSON.
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS agent_mcp_servers (
                agent_id UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
                mcp_server_id UUID NOT NULL
                    REFERENCES mcp_servers(id) ON DELETE CASCADE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (agent_id, mcp_server_id)
            )
        """)

        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_agent_mcp_servers_server
                ON agent_mcp_servers (mcp_server_id)
        """)

        await _migrate_existing_schema(conn)
        await _seed_mcp_catalog(conn)


async def _migrate_existing_schema(conn: asyncpg.Connection):
    """Bring a pre-MCP database up to date.

    CREATE TABLE IF NOT EXISTS does nothing to a table that already exists, so
    a database created before these changes keeps the old column definitions.
    These statements are guarded and safe to re-run on every startup.
    """
    # agents.kb_url used to be NOT NULL, which made a Zendesk knowledge base
    # mandatory. Harmless to repeat once already dropped.
    await conn.execute("ALTER TABLE agents ALTER COLUMN kb_url DROP NOT NULL")

    # agents.instructions was a JSONB array of {tool_name, instruction_text,
    # display_order}. Tool binding has moved to agent_mcp_servers, so the
    # column becomes the manager's prompt text. Collapse any existing
    # instruction_text values into newline-separated text so wording written
    # by hand is not thrown away.
    column_type = await conn.fetchval("""
        SELECT data_type FROM information_schema.columns
         WHERE table_schema = 'public'
           AND table_name = 'agents'
           AND column_name = 'instructions'
    """)
    if column_type == "jsonb":
        # Done as an add / backfill / drop / rename rather than
        # `ALTER COLUMN ... TYPE TEXT USING (...)`, because Postgres rejects a
        # subquery in a transform expression ("cannot use subquery in transform
        # expression") and collapsing a JSON array into one string needs an
        # aggregate over jsonb_array_elements. UPDATE has no such restriction.
        await conn.execute(
            "ALTER TABLE agents ADD COLUMN IF NOT EXISTS instructions_text TEXT"
        )
        await conn.execute("""
            UPDATE agents
               SET instructions_text = NULLIF((
                       SELECT string_agg(elem->>'instruction_text', E'\n'
                                         ORDER BY ord)
                         FROM jsonb_array_elements(instructions)
                              WITH ORDINALITY AS t(elem, ord)
                        WHERE elem->>'instruction_text' IS NOT NULL
                   ), '')
             WHERE jsonb_typeof(instructions) = 'array'
        """)
        await conn.execute("ALTER TABLE agents DROP COLUMN instructions")
        await conn.execute(
            "ALTER TABLE agents RENAME COLUMN instructions_text TO instructions"
        )
        logger.info("migrated agents.instructions from jsonb to text")


async def _seed_mcp_catalog(conn: asyncpg.Connection):
    """Upsert the supported-server catalog from mcp_catalog.py.

    Refreshes description/transport/url so an env change takes effect on the
    next boot, but deliberately leaves `wired`, `status`, `tool_count` and
    `last_error` alone — those are runtime state a redeploy must not reset.
    """
    for entry in resolved_catalog():
        await conn.execute(
            """
            INSERT INTO mcp_servers (name, description, transport, url)
                 VALUES ($1, $2, $3, $4)
            ON CONFLICT (name) DO UPDATE
                    SET description = EXCLUDED.description,
                        transport   = EXCLUDED.transport,
                        url         = EXCLUDED.url,
                        updated_at  = NOW()
            """,
            entry["name"],
            entry["description"],
            entry["transport"],
            entry["url"],
        )


async def fetch(query: str, *args):
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetch(query, *args)


async def fetchrow(query: str, *args):
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchrow(query, *args)


async def execute(query: str, *args):
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.execute(query, *args)


async def fetchval(query: str, *args):
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(query, *args)


async def close_db():
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
