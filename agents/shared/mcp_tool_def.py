"""Helper for building the McpTool used by the workflow agents."""
from __future__ import annotations

from typing import Iterable, Optional

from azure.ai.agents.models import McpTool


def build_mcp_tool(
    server_url: str,
    server_label: str = "workflow",
    allowed_tools: Optional[Iterable[str]] = None,
) -> McpTool:
    """Build an McpTool pointing at our hosted FastMCP server.

    `allowed_tools` restricts the agent to the named MCP tools (recommended
    for least-privilege). If None, the agent can call every tool the server
    exposes.

    require_approval is set to "never" so the Foundry runtime will not block
    every tool call waiting for human confirmation. The MCP server itself is
    the authoritative source of truth for what state changes are allowed.
    """
    tool = McpTool(
        server_label=server_label,
        server_url=server_url,
        allowed_tools=list(allowed_tools) if allowed_tools else [],
    )
    try:
        tool.set_approval_mode("never")
    except Exception:
        pass
    return tool
