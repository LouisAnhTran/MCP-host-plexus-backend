import asyncio
import json
import logging
from datetime import datetime
from itertools import islice

from langchain_core.tools import tool

import db
from services.embeddings import embed_batch
from services.kb.zendesk import parse_zendesk_url, fetch_sections, fetch_articles, strip_html

logger = logging.getLogger(__name__)


def _batched(iterable, n):
    it = iter(iterable)
    while chunk := list(islice(it, n)):
        yield chunk


QUESTION_STARTERS = (
    "what", "why", "how", "can", "could", "do", "does", "did",
    "is", "are", "when", "where", "who", "which", "will",
    "would", "should", "may", "might", "am",
)


def looks_like_question(title: str) -> bool:
    t = title.strip().lower()
    if t.endswith("?"):
        return True
    return any(t.startswith(s + " ") for s in QUESTION_STARTERS)


async def run_indexing_pipeline(agent_id: str):
    """Background task: fetch Zendesk articles, embed, upsert to pgvector."""
    try:
        # Small artificial delay so the indexing spinner is visible to the user
        await asyncio.sleep(8)

        agent = await db.fetchrow("SELECT * FROM agents WHERE id = $1", agent_id)
        if not agent:
            raise ValueError(f"Agent {agent_id} not found")

        base, locale, category_id = parse_zendesk_url(agent["kb_url"])

        sections = await fetch_sections(base, locale, category_id)
        articles = await fetch_articles(base, locale, category_id)

        # Clear existing vectors for this agent
        await db.execute("DELETE FROM kb_articles WHERE agent_id = $1", agent_id)

        prepared = []
        for a in articles:
            body = strip_html(a.get("body") or "")
            if not body:
                continue
            section_name = sections.get(a["section_id"], {}).get("name", "Uncategorized")
            embed_text = f"{a['title']}\n\n{body}"
            prepared.append({
                "source_article_id": a["id"],
                "article_title": a["title"],
                "article_url": a["html_url"],
                "section_name": section_name,
                "body_text": body,
                "embed_text": embed_text,
            })

        # Batch embed (100 per call)
        pool = await db.get_pool()
        for batch in _batched(prepared, 100):
            embeddings = await embed_batch([p["embed_text"] for p in batch])
            async with pool.acquire() as conn:
                async with conn.transaction():
                    for p, emb in zip(batch, embeddings):
                        await conn.execute(
                            """
                            INSERT INTO kb_articles
                                (agent_id, source_article_id, article_title, article_url,
                                 section_name, body_text, embedding)
                            VALUES ($1, $2, $3, $4, $5, $6, $7::vector)
                            ON CONFLICT (agent_id, source_article_id)
                            DO UPDATE SET
                                article_title = EXCLUDED.article_title,
                                article_url   = EXCLUDED.article_url,
                                section_name  = EXCLUDED.section_name,
                                body_text     = EXCLUDED.body_text,
                                embedding     = EXCLUDED.embedding
                            """,
                            agent_id,
                            p["source_article_id"],
                            p["article_title"],
                            p["article_url"],
                            p["section_name"],
                            p["body_text"],
                            json.dumps(emb),
                        )

        await db.execute(
            """
            UPDATE agents
            SET status = 'ready', error_message = NULL, last_indexed_at = $1
            WHERE id = $2
            """,
            datetime.utcnow(), agent_id,
        )
        logger.info(f"Indexed {len(prepared)} articles for agent {agent_id}")

    except Exception as e:
        logger.exception(f"Indexing failed for agent {agent_id}")
        await db.execute(
            "UPDATE agents SET status = 'failed', error_message = $1 WHERE id = $2",
            str(e)[:500], agent_id,
        )


def make_search_knowledge_base_tool(agent_id: str):
    @tool
    async def search_knowledge_base(query: str) -> dict:
        """Search the agent's knowledge base for answers to customer questions."""
        from services.embeddings import embed_single as _embed
        emb = await _embed(query)

        rows = await db.fetch(
            """
            SELECT id, article_title, article_url, section_name, body_text,
                   1 - (embedding <=> $1::vector) AS similarity
            FROM kb_articles
            WHERE agent_id = $2
              AND 1 - (embedding <=> $1::vector) >= 0.5
            ORDER BY embedding <=> $1::vector
            LIMIT 3
            """,
            json.dumps(emb), agent_id,
        )

        if not rows:
            return {"results": [], "related_questions": []}

        results = [
            {
                "article_title": r["article_title"],
                "article_url": r["article_url"],
                "section_name": r["section_name"],
                "body_text": r["body_text"][:2000],
                "similarity": float(r["similarity"]),
            }
            for r in rows
        ]

        top_section = rows[0]["section_name"]
        top_article_id = str(rows[0]["id"])

        related_rows = await db.fetch(
            """
            SELECT article_title, article_url
            FROM kb_articles
            WHERE agent_id = $1
              AND section_name = $2
              AND id::text != $3
            LIMIT 15
            """,
            agent_id, top_section, top_article_id,
        )

        related_questions = [
            {"question": r["article_title"], "url": r["article_url"]}
            for r in related_rows
            if looks_like_question(r["article_title"])
        ][:5]

        return {"results": results, "related_questions": related_questions}

    return search_knowledge_base
