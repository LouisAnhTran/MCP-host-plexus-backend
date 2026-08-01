import json

import asyncpg
from config import settings

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
                kb_url TEXT NOT NULL,
                instructions JSONB DEFAULT '[]',
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
