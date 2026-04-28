"""Create or update the Submissions Foundry agent. Idempotent on AGENT_NAME."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from azure.ai.projects.models import MCPTool, PromptAgentDefinition  # noqa: E402

from shared.foundry_client import get_project_client  # noqa: E402

PROJECT_ENDPOINT = "https://aif-mpwflow-dev-a3qzr7isqw476.services.ai.azure.com/api/projects/proj-mpwflow-dev"
MODEL_DEPLOYMENT_NAME = "gpt-5_3-chat"
MCP_SERVER_URL = "https://ca-mpwflow-dev-mcp-submissions.icyground-4e2c6fde.eastus2.azurecontainerapps.io/mcp"
MCP_SERVER_LABEL = "workflow"
AGENT_NAME = "submissions-agent"

# WorkIQ "Me" MCP server (Microsoft Agent 365). Uses a Foundry project
# connection so identity passthrough is handled by the connection config,
# not by us. The connection must already exist in the project.
WORKIQ_MCP_SERVER_URL = "https://agent365.svc.cloud.microsoft/agents/servers/mcp_MeServer"
WORKIQ_MCP_SERVER_LABEL = "WorkIQUser"
WORKIQ_MCP_CONNECTION_ID = "WorkIQUser"

# WorkIQ "Mail" MCP server (Microsoft Agent 365). Same identity model as
# WorkIQUser. Submissions can use it to spot prior threads about a topic
# before classifying / creating a project.
WORKIQ_MAIL_MCP_SERVER_URL = "https://agent365.svc.cloud.microsoft/agents/servers/mcp_MailTools"
WORKIQ_MAIL_MCP_SERVER_LABEL = "WorkIQMail"
WORKIQ_MAIL_MCP_CONNECTION_ID = "WorkIQMail"


def main() -> str:
    # chat-api 0.3.0: PromptAgent wires its MCP backend server-side because
    # Foundry rejects client-side tools when an agent is specified. HITL
    # approval handling is parked for a follow-up — see chat-api/README.md.
    instructions = (Path(__file__).parent / "system_prompt.md").read_text(encoding="utf-8")
    print(f"MCP server: {MCP_SERVER_URL}")

    project = get_project_client(PROJECT_ENDPOINT)
    with project:
        agents = project.agents

        mcp_tool = MCPTool(
            server_label=MCP_SERVER_LABEL,
            server_url=MCP_SERVER_URL,
            require_approval="never",
        )

        workiq_mcp_tool = MCPTool(
            server_label=WORKIQ_MCP_SERVER_LABEL,
            server_url=WORKIQ_MCP_SERVER_URL,
            require_approval="never",
            project_connection_id=WORKIQ_MCP_CONNECTION_ID,
        )

        workiq_mail_mcp_tool = MCPTool(
            server_label=WORKIQ_MAIL_MCP_SERVER_LABEL,
            server_url=WORKIQ_MAIL_MCP_SERVER_URL,
            require_approval="never",
            project_connection_id=WORKIQ_MAIL_MCP_CONNECTION_ID,
        )

        definition = PromptAgentDefinition(
            model=MODEL_DEPLOYMENT_NAME,
            instructions=instructions,
            tools=[mcp_tool, workiq_mcp_tool, workiq_mail_mcp_tool],
        )

        agent = agents.create_version(
            agent_name=AGENT_NAME,
            definition=definition,
        )

        print(f"Agent name:    {agent.name}")
        print(f"Agent id:      {getattr(agent, 'id', '<n/a>')}")
        print(f"Agent version: {getattr(agent, 'version', '<n/a>')}")
        print(f"Model:         {MODEL_DEPLOYMENT_NAME}")
        return getattr(agent, "id", agent.name)


if __name__ == "__main__":
    main()
