<h1 align="center">Plexus · MCP Host Backend</h1>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white" />
  <img src="https://img.shields.io/badge/FastAPI-005571?style=for-the-badge&logo=fastapi&logoColor=white" />
  <img src="https://img.shields.io/badge/LangGraph-1C3C3C?style=for-the-badge&logo=langchain&logoColor=white" />
  <img src="https://img.shields.io/badge/PostgreSQL-316192?style=for-the-badge&logo=postgresql&logoColor=white" />
  <img src="https://img.shields.io/badge/pgvector-4169E1?style=for-the-badge&logo=postgresql&logoColor=white" />
  <img src="https://img.shields.io/badge/Anthropic-191919?style=for-the-badge&logo=anthropic&logoColor=white" />
</p>

<p align="center">
  <em>The agent runtime behind Plexus — a platform where an agent's capabilities come from
  MCP servers discovered at runtime, not from a tool list baked into the source.</em>
</p>

---

## What this is

**Plexus** is a platform for building agents whose tools are supplied by **MCP (Model Context Protocol) servers** — both third-party servers and ones you self-host. Instead of shipping a fixed catalog of functions, you register a server, and its tools become available to any agent bound to it.

This repository is the **MCP host backend**: the FastAPI service that stores agent configuration, holds the connections to MCP servers, and runs the agent loop that calls their tools.

> [!IMPORTANT]
> **Current state: pre-MCP.** This codebase began as a customer-support meta-agent and is being reshaped into the MCP host. What runs today is the inherited runtime — a LangGraph ReAct agent over a Zendesk knowledge base with a **hardcoded tool catalog** in [`services/tools.py`](services/tools.py). No MCP client exists yet. See [Roadmap](#roadmap) for what changes and where.
>
> The rename tells you where it's going, not where it is. Read the code accordingly.

---

## Architecture

```mermaid
graph TD
    UI["Plexus Frontend<br/>React + Vite"]

    subgraph API["MCP Host Backend · FastAPI"]
        R["Routers<br/>agents · chat · mistakes · tools"]
        RT["Runtime<br/>LangGraph create_react_agent"]
        KB["KB Indexer<br/>Zendesk → embeddings"]
        FIX["Auto-fix<br/>diagnose → patch → replay"]
    end

    subgraph EXT["External"]
        LLM["Claude<br/>chat + auto-fix"]
        EMB["OpenAI<br/>text-embedding-3-small"]
        ZD["Zendesk Help Center API"]
    end

    DB[("PostgreSQL 16 + pgvector<br/>agents · kb_articles · mistake_reports")]

    UI -->|"HTTP /api/*"| R
    R --> RT
    R --> KB
    R --> FIX
    RT -->|"tool calls + completion"| LLM
    RT -->|"vector search"| DB
    KB -->|"fetch articles"| ZD
    KB -->|"embed"| EMB
    KB -->|"upsert vectors"| DB
    FIX --> LLM
    R --> DB
```

### How a chat turn works

1. `POST /api/agents/{id}/chat` loads the agent row and its `instructions` JSONB.
2. [`services/runtime.py`](services/runtime.py) builds the tool list: a per-request `search_knowledge_base` tool bound to that `agent_id`, plus any tools named by the agent's instructions.
3. [`services/prompts.py`](services/prompts.py) assembles the system prompt from a shared base template + the manager's instructions. **Nothing is stored per-agent in the DB** — the prompt is derived at request time.
4. LangGraph's `create_react_agent` runs the tool-calling loop until the model produces a final answer.
5. The response is split apart: `search_knowledge_base` results become `references`, every other tool call becomes `tool_calls`. Only messages produced *this* turn are scanned (`result["messages"][input_count:]`).

**This step 2 is the seam MCP work opens up.** Today the tool list comes from a dict in code; the goal is for it to come from `list_tools()` against connected servers.

---

## Tech stack

| Layer | Choice | Why |
|---|---|---|
| Web framework | FastAPI + Uvicorn | Async-native, OpenAPI docs for free at `/docs` |
| Agent loop | LangGraph `create_react_agent` | Pre-built ReAct loop — agent setup is ~10 lines |
| LLM | Claude via `langchain-anthropic` | Chat runtime + auto-fix diagnosis |
| Embeddings | OpenAI `text-embedding-3-small` (1536-dim) | Cheap, strong retrieval quality at this corpus size |
| Database | PostgreSQL 16 + pgvector | One store for config, vectors, and feedback |
| DB driver | `asyncpg` (no ORM) | Direct async access; the query surface is small enough that an ORM adds more than it removes |
| HTTP client | `httpx.AsyncClient` | Async throughout |
| Packaging | `uv` | Fast resolution; `uv.lock` is committed |

---

## Project structure

```
MCP-host-backend/
├── main.py                   # FastAPI app, CORS, lifespan hooks
├── db.py                     # asyncpg pool, table + index creation, query helpers
├── models.py                 # Pydantic request/response models
├── config.py                 # pydantic-settings env loading
├── Dockerfile
├── pyproject.toml / uv.lock
├── routers/
│   ├── agents.py             # CRUD + reindex          (prefix /api/agents)
│   ├── chat.py               # POST /api/agents/{id}/chat
│   ├── mistakes.py           # report + run auto-fix
│   ├── tools.py              # GET /api/tools — the catalog
│   ├── proxy.py              # GET /api/proxy-article — iframe proxy
│   └── health.py             # GET /api/health
└── services/
    ├── runtime.py            # builds tools + prompt, runs the ReAct loop
    ├── prompts.py            # BASE_SYSTEM_PROMPT + build_system_prompt()
    ├── tools.py              # ⚠️ static TOOL_CATALOG — MCP replaces this
    ├── mistakes.py           # auto-fix: diagnose → patch instructions → replay
    ├── embeddings.py         # OpenAI embedding wrapper
    └── kb/
        ├── zendesk.py        # Help Center API client + URL parsing
        └── indexer.py        # crawl → embed → upsert; builds the search tool
```

---

## Getting started

### Prerequisites

- Python 3.11+ and [`uv`](https://docs.astral.sh/uv/)
- PostgreSQL 16 with the `pgvector` extension
- An Anthropic API key and an OpenAI API key

### 1 · Database

Any Postgres 16 with pgvector works. The quickest route:

```bash
docker run -d --name plexus-db \
  -e POSTGRES_PASSWORD=changeme \
  -e POSTGRES_DB=plexus \
  -p 5432:5432 \
  pgvector/pgvector:pg16
```

Tables, the HNSW vector index, and the `vector` extension are all created on startup by `init_db()` — there is no migration step and no seed script.

### 2 · Configure

```bash
cp .env.example .env
```

| Variable | Required | Default | Notes |
|---|---|---|---|
| `DATABASE_URL` | ✅ | `postgresql://postgres:changeme@localhost:5432/plexus` | asyncpg DSN |
| `ANTHROPIC_API_KEY` | ✅ | — | Chat runtime and auto-fix |
| `OPENAI_API_KEY` | ✅ | — | Embeddings only |

`.env` is gitignored. Never commit real keys.

### 3 · Run

```bash
uv sync
source .venv/bin/activate
uvicorn main:app --reload --port 8000
```

Verify:

```bash
curl localhost:8000/api/health
# {"status":"ok","db":true,"anthropic":true,"openai":true}
```

Interactive API docs: **http://localhost:8000/docs**

### Docker

```bash
docker build -t plexus-mcp-host .
docker run -p 8000:8000 --env-file .env plexus-mcp-host
```

> [!NOTE]
> This repository is backend-only. The `docker-compose.yml`, `Caddyfile`, and frontend live outside it — bringing up the whole stack with one command requires those too.

---

## API

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/health` | Liveness + dependency checks (DB, both API keys) |
| `POST` | `/api/agents` | Create an agent; kicks off KB indexing in the background |
| `GET` | `/api/agents` | List agents |
| `GET` | `/api/agents/{id}` | Get one agent |
| `PUT` | `/api/agents/{id}` | Update; pass `reindex: true` to re-crawl the KB |
| `DELETE` | `/api/agents/{id}` | Delete (cascades to articles and reports) |
| `POST` | `/api/agents/{id}/chat` | Run a chat turn |
| `POST` | `/api/agents/{id}/mistakes` | Report a bad answer |
| `GET` | `/api/agents/{id}/mistakes` | List reports for an agent |
| `PUT` | `/api/mistakes/{id}/fix` | Run auto-fix and verify by replay |
| `GET` | `/api/tools` | The tool catalog |
| `GET` | `/api/proxy-article` | Proxy a Zendesk article (bypasses `X-Frame-Options`) |

<details>
<summary><strong>Chat response shape</strong></summary>

```json
{
  "reply": "...",
  "references": [{ "article_title": "...", "article_url": "..." }],
  "related_questions": [{ "question": "...", "url": "..." }],
  "tool_calls": [{ "name": "...", "args": {} }]
}
```

`references` come exclusively from `search_knowledge_base`; every other tool lands in `tool_calls`. The two never mix.

</details>

---

## Database schema

Created idempotently on startup by [`db.py`](db.py):

| Table | Columns of note |
|---|---|
| `agents` | `id` uuid pk · `name` · `kb_url` not null · `instructions` jsonb · `status` · `error_message` · `last_indexed_at` |
| `kb_articles` | `agent_id` fk cascade · `source_article_id` · `article_title` · `article_url` · `section_name` · `body_text` · `embedding vector(1536)` · unique `(agent_id, source_article_id)` |
| `mistake_reports` | `agent_id` fk cascade · `user_message` · `bot_response` · `user_description` · `status` · `fix_comment` · `verified_response` |

Indexes: HNSW on `kb_articles.embedding` (`vector_cosine_ops`) for similarity search, plus a B-tree on `(agent_id, section_name)` for the same-section lookup that powers related questions.

---

## Design decisions

Carried over from the original project and still load-bearing:

- **No `system_prompt` column.** A shared base template lives in code; per-agent instructions are appended at request time. One template to change, no drift across rows.
- **Zendesk API, not HTML scraping.** Clean JSON, no Cloudflare fights, works against any Zendesk help center. Non-Zendesk URLs are rejected with a 400.
- **One article = one embedding.** Title and body embedded together, no chunking. Help-center articles are already short and self-contained.
- **`search_knowledge_base` is just a tool.** No router or classifier decides between "answer from KB" and "call a business tool" — the model picks, same as any other tool call. This is the design assumption MCP integration builds directly on.
- **Async indexing with crash recovery.** Indexing runs via `asyncio.create_task` and tracks state in `agents.status`. On startup, any agent stuck in `indexing` from a killed process is marked `failed` rather than hanging forever.
- **Tools never raise.** Every tool implementation returns a dict; failures come back as `{"error": ..., "message": ...}` so the model can react instead of the request 500-ing.

---

## Roadmap

Turning this into an actual MCP host, roughly in dependency order:

- [ ] **`mcp_servers` table** — transport (stdio / HTTP+SSE), command or URL, auth config, enabled flag
- [ ] **MCP client layer** — connect, handshake, `list_tools()`, `call_tool()`, with connection pooling and reconnect
- [ ] **Runtime tool discovery** — replace the static `TOOL_CATALOG` with tools resolved from connected servers, adapted into LangChain tools
- [ ] **Server↔agent binding** — which agents may reach which servers, and which tools within them
- [ ] **Credential storage** — per-server secrets, encrypted at rest, never returned by the API
- [ ] **Self-hosted server registry** — first-party MCP servers deployed alongside the host
- [ ] **Generalize beyond Zendesk** — the KB becomes one MCP server among many, not a special case
- [ ] **Tests** — `pytest` + `pytest-asyncio` are already dev dependencies and currently unused

---

## Notes

- **Model IDs.** The chat runtime and auto-fix both pin `claude-opus-4-6` ([`services/runtime.py`](services/runtime.py), [`services/mistakes.py`](services/mistakes.py)). Newer models are available — `claude-opus-5` is current — but moving up is a deliberate change, not a find-and-replace: Opus 5 enables thinking by default and shifts token accounting, so `max_tokens` (currently 2048) and prompt behavior both want revisiting.
- **CORS is wide open** (`allow_origins=["*"]` in [`main.py`](main.py)). Fine for local development, not for a deployment with real data behind it.
- **The 3-second sleep** at the top of `run_chat()` is deliberate — it holds the frontend's "reasoning" indicator on screen long enough to be seen. Delete it if you're driving this API directly.

---

<p align="center"><sub>Forked from a CS meta-agent · being rebuilt as an MCP host</sub></p>
