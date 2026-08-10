"""The MCP servers this application knows how to talk to.

Declared in code rather than entered by users. That keeps the UI a checkbox
list instead of a free-text URL field, and means nobody can point an agent at
an arbitrary MCP server they control.

Each entry is seeded into the `mcp_servers` table on startup with
`wired = false`. Ticking the checkbox is what connects, discovers tools, and
flips `wired` to true — see services/mcp_registry.py.

URLs come from the environment because the same address differs by deployment:

    host on the machine, MCP server in Docker   http://localhost:9000/mcp
    both in the same compose project            http://einvoice-mcp:9000/mcp
    both in Kubernetes                          http://einvoice-mcp:9000/mcp

Adding support for a new server is one entry here plus its URL env var. The
tools it offers are never listed — those are discovered at runtime via
`tools/list`, which is the whole point of MCP.
"""

import os

SUPPORTED_MCP_SERVERS: list[dict] = [
    {
        "name": "einvoice",
        "description": (
            "Register companies on the InvoiceNow (Peppol) e-invoicing network "
            "and manage their tax-submission status."
        ),
        "transport": "http",
        "url_env": "MCP_EINVOICE_URL",
        "url_default": "http://localhost:9000/mcp",
    },
]


def resolved_catalog() -> list[dict]:
    """The catalog with each URL resolved from the environment."""
    return [
        {
            "name": entry["name"],
            "description": entry["description"],
            "transport": entry["transport"],
            "url": os.getenv(entry["url_env"], entry["url_default"]),
        }
        for entry in SUPPORTED_MCP_SERVERS
    ]
