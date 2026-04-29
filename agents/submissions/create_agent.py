"""Create or update the Submissions Foundry agent. Idempotent on AGENT_NAME.

Reads from environment so the same script works against any project:
  FOUNDRY_PROJECT_ENDPOINT  - e.g. https://<aif>.services.ai.azure.com/api/projects/<proj>
  MODEL_DEPLOYMENT_NAME     - e.g. gpt-4o-mini
  SUBMISSIONS_MCP_URL       - e.g. https://<ca-fqdn>/mcp
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from azure.ai.projects.models import MCPTool, PromptAgentDefinition  # noqa: E402

from shared.foundry_client import get_project_client  # noqa: E402

AGENT_NAME = "submissions-agent"
MCP_SERVER_LABEL = "workflow"

# WorkIQ catalog MCP servers (Microsoft Agent 365). Identity passthrough is
# handled by the matching Foundry project connection (created in the
# Foundry portal — see scripts/admin/README.md). Submissions uses these to
# scan prior threads about a topic before classifying / creating a project.
WORKIQ_USER_URL = "https://agent365.svc.cloud.microsoft/agents/servers/mcp_MeServer"
WORKIQ_USER_LABEL = "WorkIQUser"
WORKIQ_USER_CONNECTION_ID = "WorkIQUser"

WORKIQ_MAIL_URL = "https://agent365.svc.cloud.microsoft/agents/servers/mcp_MailTools"
WORKIQ_MAIL_LABEL = "WorkIQMail"
WORKIQ_MAIL_CONNECTION_ID = "WorkIQMail"


def _require_env(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        sys.stderr.write(f"ERROR: required env var {name!r} is not set.\n")
        sys.exit(2)
    return val


def main() -> str:
    endpoint = _require_env("FOUNDRY_PROJECT_ENDPOINT")
    model = _require_env("MODEL_DEPLOYMENT_NAME")
    mcp_url = _require_env("SUBMISSIONS_MCP_URL")
    instructions = (Path(__file__).parent / "system_prompt.md").read_text(encoding="utf-8")

    print(f"endpoint:   {endpoint}")
    print(f"model:      {model}")
    print(f"MCP server: {mcp_url}")

    project = get_project_client(endpoint)
    with project:
        agents = project.agents

        mcp_tool = MCPTool(
            server_label=MCP_SERVER_LABEL,
            server_url=mcp_url,
            require_approval="never",
        )

        workiq_user_tool = MCPTool(
            server_label=WORKIQ_USER_LABEL,
            server_url=WORKIQ_USER_URL,
            require_approval="never",
            project_connection_id=WORKIQ_USER_CONNECTION_ID,
        )

        workiq_mail_tool = MCPTool(
            server_label=WORKIQ_MAIL_LABEL,
            server_url=WORKIQ_MAIL_URL,
            require_approval="never",
            project_connection_id=WORKIQ_MAIL_CONNECTION_ID,
        )

        definition = PromptAgentDefinition(
            model=model,
            instructions=instructions,
            tools=[mcp_tool, workiq_user_tool, workiq_mail_tool],
        )

        agent = agents.create_version(
            agent_name=AGENT_NAME,
            definition=definition,
        )

        print(f"Agent name:    {agent.name}")
        print(f"Agent id:      {getattr(agent, 'id', '<n/a>')}")
        print(f"Agent version: {getattr(agent, 'version', '<n/a>')}")
        return getattr(agent, "id", agent.name)


if __name__ == "__main__":
    main()
