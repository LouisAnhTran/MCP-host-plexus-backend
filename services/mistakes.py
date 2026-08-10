"""Mistake reports.

Reporting a bad answer still works and still records the exchange. The
automated repair does not, and has been removed rather than left in place
behind a dead branch.

Why it cannot work any more: it operated by asking the model to rewrite the
`instruction_text` of an agent's instructions, each of which was bound to one
tool. Capabilities now come from MCP servers, tool descriptions come from the
servers themselves, and there is no per-agent text left for a fix to edit.

Reinstating something equivalent would mean choosing a new thing to tune — the
system prompt template, or per-agent guidance if that is reintroduced — which is
a design decision, not a port.
"""

import logging

logger = logging.getLogger(__name__)


class AutoFixUnavailable(Exception):
    """Auto-fix has no mechanism under the MCP model."""


async def apply_fix(mistake_id: str) -> dict:
    raise AutoFixUnavailable(
        "Auto-fix is unavailable: it worked by rewriting per-agent instruction "
        "text, which no longer exists now that agent capabilities come from MCP "
        "servers. The report itself has been kept."
    )
