"""System prompt assembly.

The prompt is derived per request, not stored per agent — one template here plus
the agent's name and whichever capabilities it actually has. Storing a rendered
prompt per agent would mean every wording change needed a data migration.

Two things deliberately absent:

  * Per-agent instruction text. It existed to bind tools; MCP servers do that
    now, and tool descriptions come from the servers themselves.
  * Any list of tools. The model is handed the tool schemas by LangGraph and
    decides which to call. Restating them in the prompt would duplicate the
    schemas and go stale the moment a server is redeployed.
"""

BASE_SYSTEM_PROMPT = """You are a helpful assistant for {agent_name}.

Answer the user's question, using the tools available to you when they apply.
Decide for yourself which tool fits — each one's description says what it does
and when to use it.

BEHAVIORAL RULES:
- Be direct and concise. Skip preamble.
- When a tool needs information you do not have, ask the user for it rather
  than guessing or inventing a value.
- Never fabricate a result. If a tool fails, say what failed and what it means
  for the user, using the error the tool returned.
- Some operations are asynchronous and settle after a delay. When a result says
  something is pending, tell the user it is in progress and roughly how long,
  rather than reporting it as finished or retrying immediately.
- Before an irreversible action, confirm with the user unless they have already
  asked for it explicitly.
{kb_section}
FORMATTING:
- Replies are rendered as Markdown.
- Put each item of a list on its own line. Never run list items together in a
  single sentence.
- Separate paragraphs with a blank line.
"""

KB_SECTION = """
KNOWLEDGE BASE:
- You have a knowledge base. For questions it could answer, search it BEFORE
  responding.
- When you answer from the knowledge base, end your reply with a citation
  block: the heading line `**Sources:**` on its own line, then a Markdown
  bulleted list, one article per line, in the form `- [Article Title](https://...)`.
  Never put two articles on one line, never paste a raw URL, and never omit the
  heading.
- If the knowledge base does not cover the question and no tool applies, say
  so. Do not offer to escalate to a human unless the user asks for one.
- Do not add a "related questions" or "you might also like" section — the UI
  already shows those as clickable chips.
"""


def build_system_prompt(agent: dict) -> str:
    """Assemble the system prompt for one chat request.

    The knowledge-base rules are only included when the agent actually has a
    knowledge base. Telling an MCP-only agent to "search the knowledge base
    first" would have it apologise for a tool it was never given.
    """
    has_kb = bool(agent.get("kb_url"))
    return BASE_SYSTEM_PROMPT.format(
        agent_name=agent["name"],
        kb_section=KB_SECTION if has_kb else "",
    )
