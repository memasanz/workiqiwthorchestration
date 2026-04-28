"""Create or update the Legal SME Foundry agent.

Idempotent: ``agents.create_version`` on the same ``agent_name`` rolls a new
version of the existing agent rather than creating a duplicate.
"""
from __future__ import annotations

import sys
from pathlib import Path

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import MCPTool, PromptAgentDefinition
from azure.identity import DefaultAzureCredential


PROJECT_ENDPOINT = (
    "https://aif-mpwflow-dev-a3qzr7isqw476.services.ai.azure.com"
    "/api/projects/proj-mpwflow-dev"
)
MODEL_DEPLOYMENT_NAME = "gpt-5_3-chat"
MCP_SERVER_URL = (
    "https://ca-mpwflow-dev-mcp-tax.icyground-4e2c6fde.eastus2.azurecontainerapps.io/mcp"
)
MCP_SERVER_LABEL = "mpwflow"
AGENT_NAME = "tax-simple-agent"

MCP_SERVER_LABEL = "mpwflow"
WORKIQ_MCP_SERVER_URL = "https://agent365.svc.cloud.microsoft/agents/servers/mcp_MeServer"
WORKIQ_MCP_SERVER_LABEL = "WorkIQUser"
WORKIQ_MCP_CONNECTION_ID = "WorkIQUser"



def load_instructions() -> str:
    print( (Path(__file__).parent / "tax_system_prompt.md").read_text(encoding="utf-8"))
    return (Path(__file__).parent / "tax_system_prompt.md").read_text(encoding="utf-8")



def main() -> int:
    # chat-api 0.3.0: PromptAgent wires its MCP backend server-side because
    # Foundry rejects client-side tools when an agent is specified.
    instructions = load_instructions()

    client = AIProjectClient(
        endpoint=PROJECT_ENDPOINT,
        credential=DefaultAzureCredential(),
    )

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

    definition = PromptAgentDefinition(
        model=MODEL_DEPLOYMENT_NAME,
        instructions=instructions,
        tools=[mcp_tool, workiq_mcp_tool],
    )

    agent = client.agents.create_version(
        agent_name=AGENT_NAME,
        definition=definition,
    )

    print(f"Agent name:    {agent.name}")
    print(f"Agent id:      {getattr(agent, 'id', '<n/a>')}")
    print(f"Agent version: {getattr(agent, 'version', '<n/a>')}")
    print(f"Model:         {MODEL_DEPLOYMENT_NAME}")
    print(f"MCP server:    {MCP_SERVER_URL}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
