import json
import logging

from langchain_anthropic import ChatAnthropic
from pydantic import BaseModel, Field

import db
from config import settings
from services.runtime import run_chat
from services.tools import TOOL_CATALOG

logger = logging.getLogger(__name__)

FIX_SYSTEM_PROMPT = """You are an expert at refining customer service agent instructions.

A customer reported that the agent gave a bad response. Your job is to identify which EXISTING
instruction(s) need their text revised to prevent this mistake in the future.

Rules:
- You may ONLY propose new `instruction_text` for instructions that already exist.
- Identify each instruction to update by its existing `tool_name`.
- Do NOT invent new tool names, do NOT add new instructions, do NOT remove instructions,
  do NOT change `tool_name` or `display_order`.
- If an existing instruction's text is already fine, leave it out of `updates`.
- `updates` may be empty if no text changes are needed — just explain in `fix_comment`.
- Keep each revised text concise, specific, and actionable.
- Also provide a short `fix_comment` (one to two sentences) explaining what you changed and why.
"""


class InstructionTextUpdate(BaseModel):
    tool_name: str = Field(
        description="tool_name of an EXISTING instruction to update. Must match one of the agent's current instruction tool_names exactly."
    )
    instruction_text: str = Field(
        description="The new instruction_text to replace the current text for this tool."
    )


class FixOutput(BaseModel):
    updates: list[InstructionTextUpdate] = Field(
        default_factory=list,
        description=(
            "Per-instruction text updates. Only include instructions that need their text "
            "revised. Leave empty if no changes are needed."
        ),
    )
    fix_comment: str = Field(
        description="Brief (1-2 sentence) explanation of what was changed and why."
    )


def _build_user_prompt(agent: dict, mistake: dict) -> str:
    current_instructions = agent["instructions"] or []
    existing_tool_names = sorted(
        {ins["tool_name"] for ins in current_instructions if ins.get("tool_name")}
    )
    return f"""AGENT NAME: {agent['name']}

CURRENT INSTRUCTIONS:
{json.dumps(current_instructions, indent=2)}

VALID tool_name VALUES FOR `updates` (only these existing tool_names can be updated):
{json.dumps(existing_tool_names)}

USER QUESTION:
{mistake['user_message']}

BAD BOT RESPONSE:
{mistake['bot_response']}

USER-REPORTED ISSUE:
{mistake['user_description'] or '(no description provided)'}

Propose text updates (if any) and a fix_comment."""


def _validate(fix: FixOutput, existing_tool_names: set[str]) -> None:
    seen: set[str] = set()
    for upd in fix.updates:
        if not upd.instruction_text or not upd.instruction_text.strip():
            raise ValueError(f"LLM returned empty instruction_text for tool {upd.tool_name!r}")
        if upd.tool_name not in TOOL_CATALOG:
            raise ValueError(f"LLM proposed unknown tool: {upd.tool_name!r}")
        if upd.tool_name not in existing_tool_names:
            raise ValueError(
                f"LLM proposed updating tool {upd.tool_name!r}, which is not bound to any existing instruction"
            )
        if upd.tool_name in seen:
            raise ValueError(f"LLM proposed multiple updates for the same tool: {upd.tool_name!r}")
        seen.add(upd.tool_name)
    if not fix.fix_comment or not fix.fix_comment.strip():
        raise ValueError("LLM returned empty fix_comment")


async def apply_fix(mistake_id: str) -> dict:
    """Analyze a mistake report, update the agent's instructions, and verify via replay."""

    mistake = await db.fetchrow(
        "SELECT * FROM mistake_reports WHERE id = $1", mistake_id
    )
    if not mistake:
        raise ValueError(f"Mistake {mistake_id} not found")
    if mistake["status"] == "fixed":
        raise ValueError("This mistake has already been fixed")

    agent = await db.fetchrow(
        "SELECT * FROM agents WHERE id = $1", mistake["agent_id"]
    )
    if not agent:
        raise ValueError(f"Agent {mistake['agent_id']} not found")

    llm = ChatAnthropic(
        model="claude-opus-4-6",
        api_key=settings.anthropic_api_key,
        max_tokens=2048,
    ).with_structured_output(FixOutput)

    agent_dict = dict(agent)
    current_instructions = agent_dict["instructions"] or []
    existing_tool_names = {
        ins["tool_name"] for ins in current_instructions if ins.get("tool_name")
    }

    fix: FixOutput = await llm.ainvoke([
        {"role": "system", "content": FIX_SYSTEM_PROMPT},
        {"role": "user", "content": _build_user_prompt(agent_dict, dict(mistake))},
    ])
    _validate(fix, existing_tool_names)

    # Merge: preserve every existing instruction as-is; only overwrite
    # instruction_text for entries whose tool_name appears in updates.
    updates_by_tool = {upd.tool_name: upd.instruction_text for upd in fix.updates}
    new_instructions = [
        {**ins, "instruction_text": updates_by_tool[ins["tool_name"]]}
        if ins.get("tool_name") in updates_by_tool
        else dict(ins)
        for ins in current_instructions
    ]

    if updates_by_tool:
        await db.execute(
            """
            UPDATE agents
            SET instructions = $1, updated_at = NOW()
            WHERE id = $2
            """,
            new_instructions, mistake["agent_id"],
        )

    # Verification replay — run the original question through the now-updated agent.
    try:
        replay = await run_chat(
            str(mistake["agent_id"]),
            [{"role": "user", "content": mistake["user_message"]}],
        )
        verified_response = replay["reply"]
    except Exception as e:
        logger.exception("Verification replay failed")
        verified_response = f"(verification failed: {e})"

    updated = await db.fetchrow(
        """
        UPDATE mistake_reports
        SET status = 'fixed',
            fix_comment = $1,
            verified_response = $2,
            resolved_at = NOW()
        WHERE id = $3
        RETURNING *
        """,
        fix.fix_comment, verified_response, mistake_id,
    )
    return dict(updated)
