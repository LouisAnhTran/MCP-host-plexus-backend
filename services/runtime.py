import asyncio
import json
import logging

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage
from langgraph.prebuilt import create_react_agent

import db
from config import settings
from services import mcp_registry
from services.kb.indexer import make_search_knowledge_base_tool
from services.prompts import build_system_prompt

logger = logging.getLogger(__name__)


def _get_llm():
    return ChatAnthropic(
        model="claude-opus-4-6",
        api_key=settings.anthropic_api_key,
        max_tokens=2048,
    )


def _to_lc_messages(messages: list[dict]):
    result = []
    for m in messages:
        role = m.get("role")
        content = m.get("content", "")
        if role == "user":
            result.append(HumanMessage(content=content))
        elif role == "assistant":
            result.append(AIMessage(content=content))
    return result


async def run_chat(agent_id: str, messages: list[dict]) -> dict:
    # Short pause so the frontend reasoning indicator is visibly shown
    # before the response lands.
    await asyncio.sleep(3)

    agent_row = await db.fetchrow("SELECT * FROM agents WHERE id = $1", agent_id)
    if not agent_row:
        raise ValueError(f"Agent {agent_id} not found")

    agent = dict(agent_row)

    # 1. Native tool: knowledge-base search, bound to this agent_id.
    #    Stays in-process rather than behind MCP because it is agent-scoped by
    #    construction (the model never sees or supplies the agent id), it reads
    #    the same database this service owns, and its results feed the
    #    `references` contract below.
    tools = []
    if agent.get("kb_url"):
        tools.append(make_search_knowledge_base_tool(agent_id))

    # 2. MCP tools: everything the servers this agent is bound to expose.
    #    Read from the in-memory cache — no tools/list round trip per turn.
    server_names = [
        row["name"]
        for row in await db.fetch(
            """
            SELECT s.name
              FROM mcp_servers s
              JOIN agent_mcp_servers ams ON ams.mcp_server_id = s.id
             WHERE ams.agent_id = $1
             ORDER BY s.name
            """,
            agent_id,
        )
    ]
    mcp_tools = mcp_registry.tools_for(server_names)
    tools.extend(mcp_tools)

    logger.info(
        "agent %s: %d tool(s) — kb=%s, mcp servers=%s",
        agent["name"],
        len(tools),
        bool(agent.get("kb_url")),
        server_names or "none",
    )

    # 3. Build system prompt. It does not enumerate the tools — LangGraph hands
    #    their schemas to the model, which decides what to call.
    system_prompt = build_system_prompt(agent)

    # 4. Create ReAct agent
    executor = create_react_agent(
        model=_get_llm(),
        tools=tools,
        prompt=system_prompt,
    )

    # 5. Run
    lc_messages = _to_lc_messages(messages)
    input_count = len(lc_messages)
    result = await executor.ainvoke({"messages": lc_messages})

    # 6. Extract reply + references + related questions + tool calls
    #    Only scan messages produced THIS turn (after input_count)
    new_messages = result["messages"][input_count:]

    reply_text = ""
    for msg in reversed(new_messages):
        if isinstance(msg, AIMessage) and msg.content:
            if isinstance(msg.content, str):
                reply_text = msg.content
            elif isinstance(msg.content, list):
                # content blocks — find text block
                for block in msg.content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        reply_text = block["text"]
                        break
            if reply_text:
                break

    tool_calls_log = []
    references = []
    related_questions = []

    for msg in new_messages:
        # AI message with tool_calls
        if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
            for tc in msg.tool_calls:
                if tc["name"] != "search_knowledge_base":
                    tool_calls_log.append({"name": tc["name"], "args": tc["args"]})

        # Tool result message
        if isinstance(msg, ToolMessage) and msg.name == "search_knowledge_base":
            try:
                content = msg.content
                result_data = json.loads(content) if isinstance(content, str) else content
                if isinstance(result_data, dict):
                    for r in result_data.get("results", []):
                        references.append({
                            "article_title": r["article_title"],
                            "article_url": r["article_url"],
                        })
                    related_questions = result_data.get("related_questions", [])
            except Exception:
                pass

    return {
        "reply": reply_text,
        "references": references,
        "related_questions": related_questions,
        "tool_calls": tool_calls_log,
    }
